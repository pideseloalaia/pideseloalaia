"""
Publica en Instagram el contenido de la carpeta del dia.

Estructura esperada:
    contenido/AAAA-MM-DD/
        caption.txt          texto del post (opcional)
        01.jpg, 02.jpg ...   imagenes (1 = post simple, 2 a 10 = carrusel)
        video.mp4            un solo video = Reel

Si la carpeta tiene imagenes y videos mezclados se arma un carrusel.
Al terminar deja un publicado.json en la carpeta para no repetir el post.
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

GRAPH_VERSION = os.environ.get("GRAPH_VERSION") or "v25.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"

CARPETA_BASE = Path(os.environ.get("CARPETA_CONTENIDO") or "contenido")
ZONA = ZoneInfo(os.environ.get("ZONA_HORARIA") or "America/Argentina/Buenos_Aires")
DRY_RUN = (os.environ.get("DRY_RUN") or "false").lower() == "true"
MEDIA_BASE_URL = (os.environ.get("MEDIA_BASE_URL") or "").rstrip("/")

IMAGENES = {".jpg", ".jpeg"}
CONVERTIBLES = {".png", ".webp"}
VIDEOS = {".mp4", ".mov"}
IGNORADOS = {"caption.txt", "publicado.json", "readme.md", ".gitkeep"}

MAX_CARRUSEL = 10
MAX_CAPTION = 2200
ESPERA_MAXIMA_SEG = 900


def log(mensaje):
    print(mensaje, flush=True)


def fallar(mensaje):
    print(f"::error::{mensaje}", flush=True)
    sys.exit(1)


# ---------------------------------------------------------------- HTTP

def llamar(metodo, url, params=None, cuerpo=None, headers=None):
    token = os.environ["IG_ACCESS_TOKEN"]
    headers = dict(headers or {})
    headers.setdefault("Authorization", f"Bearer {token}")

    if params and metodo == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
    elif params:
        cuerpo = urllib.parse.urlencode(params).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=cuerpo, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detalle = e.read().decode(errors="replace")
        raise RuntimeError(f"{metodo} {url.split('?')[0]} -> {e.code}: {detalle}") from None


def graph_post(ruta, params):
    return llamar("POST", f"{GRAPH}/{ruta}", params=params)


def graph_get(ruta, params):
    return llamar("GET", f"{GRAPH}/{ruta}", params=params)


# ---------------------------------------------------------------- Git

def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def commit_y_push(rutas, mensaje):
    git("add", "-A", *[str(r) for r in rutas])
    if not git("status", "--porcelain", *[str(r) for r in rutas]):
        return git("rev-parse", "HEAD")
    git("commit", "-m", mensaje)
    git("pull", "--rebase", "--autostash")
    git("push")
    return git("rev-parse", "HEAD")


# ---------------------------------------------------------------- Archivos

def orden_natural(ruta):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r"(\d+)", ruta.name)]


def listar_medios(carpeta):
    medios = []
    for archivo in sorted(carpeta.iterdir(), key=orden_natural):
        if not archivo.is_file() or archivo.name.lower() in IGNORADOS:
            continue
        ext = archivo.suffix.lower()
        if ext in IMAGENES | CONVERTIBLES | VIDEOS:
            medios.append(archivo)
        else:
            log(f"Se ignora {archivo.name}: formato no soportado")
    return medios


def convertir_a_jpeg(medios):
    """Instagram solo acepta JPEG en imagenes. Convierte PNG/WEBP y borra el original."""
    from PIL import Image

    resultado = []
    for archivo in medios:
        if archivo.suffix.lower() not in CONVERTIBLES:
            resultado.append(archivo)
            continue
        destino = archivo.with_suffix(".jpg")
        with Image.open(archivo) as img:
            if img.mode in ("RGBA", "LA", "P"):
                img = img.convert("RGBA")
                fondo = Image.new("RGB", img.size, (255, 255, 255))
                fondo.paste(img, mask=img.split()[-1])
                img = fondo
            else:
                img = img.convert("RGB")
            img.save(destino, "JPEG", quality=95)
        archivo.unlink()
        log(f"Convertido {archivo.name} -> {destino.name}")
        resultado.append(destino)
    return resultado


def url_publica(archivo, sha):
    relativa = "/".join(urllib.parse.quote(p) for p in archivo.as_posix().split("/"))
    if MEDIA_BASE_URL:
        return f"{MEDIA_BASE_URL}/{relativa}"
    repo = os.environ["GITHUB_REPOSITORY"]
    return f"https://raw.githubusercontent.com/{repo}/{sha}/{relativa}"


def es_video(archivo):
    return archivo.suffix.lower() in VIDEOS


# ---------------------------------------------------------------- Instagram

def contenedor_imagen(ig_user, url, caption=None, en_carrusel=False):
    params = {"image_url": url}
    if en_carrusel:
        params["is_carousel_item"] = "true"
    elif caption:
        params["caption"] = caption
    return graph_post(f"{ig_user}/media", params)["id"]


def contenedor_video(ig_user, archivo, caption=None, en_carrusel=False):
    params = {"upload_type": "resumable"}
    if en_carrusel:
        params["media_type"] = "VIDEO"
        params["is_carousel_item"] = "true"
    else:
        params["media_type"] = "REELS"
        params["share_to_feed"] = "true"
        if caption:
            params["caption"] = caption

    contenedor = graph_post(f"{ig_user}/media", params)["id"]

    datos = archivo.read_bytes()
    log(f"Subiendo {archivo.name} ({len(datos) / 1_048_576:.1f} MB)")
    llamar(
        "POST",
        f"{RUPLOAD}/{contenedor}",
        cuerpo=datos,
        headers={
            "Authorization": f"OAuth {os.environ['IG_ACCESS_TOKEN']}",
            "offset": "0",
            "file_size": str(len(datos)),
        },
    )
    return contenedor


def esperar_listo(contenedor):
    inicio = time.time()
    while True:
        estado = graph_get(contenedor, {"fields": "status_code,status"})
        codigo = estado.get("status_code")
        if codigo in ("FINISHED", "PUBLISHED"):
            return
        if codigo in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"Contenedor {contenedor} en estado {codigo}: {estado.get('status')}")
        if time.time() - inicio > ESPERA_MAXIMA_SEG:
            raise RuntimeError(f"Contenedor {contenedor} sigue en {codigo} despues de {ESPERA_MAXIMA_SEG}s")
        log(f"  contenedor {contenedor}: {codigo}, esperando...")
        time.sleep(10)


def publicar(ig_user, medios, caption, sha):
    if len(medios) == 1:
        archivo = medios[0]
        if es_video(archivo):
            contenedor = contenedor_video(ig_user, archivo, caption)
        else:
            contenedor = contenedor_imagen(ig_user, url_publica(archivo, sha), caption)
        esperar_listo(contenedor)
    else:
        hijos = []
        for archivo in medios:
            log(f"Preparando {archivo.name}")
            if es_video(archivo):
                hijo = contenedor_video(ig_user, archivo, en_carrusel=True)
            else:
                hijo = contenedor_imagen(ig_user, url_publica(archivo, sha), en_carrusel=True)
            esperar_listo(hijo)
            hijos.append(hijo)

        params = {"media_type": "CAROUSEL", "children": ",".join(hijos)}
        if caption:
            params["caption"] = caption
        contenedor = graph_post(f"{ig_user}/media", params)["id"]
        esperar_listo(contenedor)

    media_id = graph_post(f"{ig_user}/media_publish", {"creation_id": contenedor})["id"]
    try:
        permalink = graph_get(media_id, {"fields": "permalink"}).get("permalink")
    except RuntimeError:
        permalink = None
    return media_id, permalink


# ---------------------------------------------------------------- Main

def main():
    fecha = (os.environ.get("FECHA") or "").strip() or datetime.now(ZONA).strftime("%Y-%m-%d")
    # Acepta "2026-11-23" o "2026-11-23_4-casa-limpia"
    candidatas = sorted(
        c for c in CARPETA_BASE.glob(f"{fecha}*") if c.is_dir()
    ) if CARPETA_BASE.is_dir() else []

    if not candidatas:
        print(f"::notice::No hay contenido para {fecha}. Nada que publicar.")
        return
    if len(candidatas) > 1:
        fallar(f"Hay {len(candidatas)} carpetas para {fecha}: {', '.join(c.name for c in candidatas)}. Dejá una sola por día.")

    carpeta = candidatas[0]
    log(f"Fecha: {fecha} | carpeta: {carpeta}")

    marca = carpeta / "publicado.json"
    if marca.exists():
        print(f"::notice::El contenido de {fecha} ya se publico: {marca.read_text().strip()}")
        return

    archivo_caption = carpeta / "caption.txt"
    caption = archivo_caption.read_text(encoding="utf-8").strip() if archivo_caption.exists() else ""
    if len(caption) > MAX_CAPTION:
        fallar(f"El caption tiene {len(caption)} caracteres y el limite de Instagram es {MAX_CAPTION}")

    medios = listar_medios(carpeta)
    if not medios:
        fallar(f"La carpeta {carpeta} no tiene imagenes ni videos")
    if len(medios) > MAX_CARRUSEL:
        fallar(f"Hay {len(medios)} archivos y un carrusel admite hasta {MAX_CARRUSEL}")
    if any(m.suffix.lower() in CONVERTIBLES for m in medios):
        medios = convertir_a_jpeg(medios)
        if not DRY_RUN:
            commit_y_push([carpeta], f"Convierte imagenes de {fecha} a JPEG")

    sha = git("rev-parse", "HEAD")

    tipo = "Reel" if len(medios) == 1 and es_video(medios[0]) else (
        "imagen" if len(medios) == 1 else f"carrusel de {len(medios)}")
    log(f"Tipo de post: {tipo}")
    for m in medios:
        log(f"  - {m.name}" + ("" if es_video(m) else f"  {url_publica(m, sha)}"))
    log(f"Caption: {caption[:120]}{'...' if len(caption) > 120 else ''}")

    if DRY_RUN:
        log("Modo prueba: no se publica nada.")
        return

    ig_user = os.environ["IG_USER_ID"]
    try:
        media_id, permalink = publicar(ig_user, medios, caption, sha)
    except RuntimeError as e:
        fallar(str(e))

    log(f"Publicado. media_id={media_id} {permalink or ''}")
    marca.write_text(
        json.dumps(
            {
                "media_id": media_id,
                "permalink": permalink,
                "tipo": tipo,
                "publicado_en": datetime.now(ZONA).isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    commit_y_push([marca], f"Publicado en Instagram: {fecha}")


if __name__ == "__main__":
    main()
