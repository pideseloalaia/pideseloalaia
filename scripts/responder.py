"""Responde automaticamente los comentarios que traen la palabra clave del post.

Cada carpeta de contenido/ que ya se publico (tiene publicado.json) puede tener
un respuesta.txt con su propia palabra clave:

    PALABRA: RECETA
    COMENTARIO: Listo {usuario}, te lo mande por mensaje privado.
    COMENTARIO: Ya te llego al privado, {usuario}. Fijate en tus mensajes.
    MENSAJE:
    Hola {usuario}! Gracias por comentar.
    Aca tenes la guia: https://...

- PALABRA admite varias separadas por coma. No distingue mayusculas ni tildes.
- COMENTARIO puede repetirse: se elige uno al azar en cada respuesta, para que
  Instagram no las tome como spam por ser todas iguales.
- MENSAJE es el mensaje privado y sigue hasta el final del archivo. Si no hay
  MENSAJE, solo se responde el comentario en publico.

Se revisan los posts publicados en los ultimos DIAS_ACTIVOS dias (7 por defecto,
que es el plazo que da Instagram para mandar un privado a quien comento).
Lo ya respondido se guarda en estado/respondidos.json para no repetir.
"""

import json
import os
import random
import re
import subprocess
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

GRAPH_VERSION = os.environ.get("GRAPH_VERSION") or "v25.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"

CARPETA_BASE = Path(os.environ.get("CARPETA_CONTENIDO") or "contenido")
ARCHIVO_ESTADO = Path(os.environ.get("ARCHIVO_ESTADO") or "estado/respondidos.json")
ZONA = ZoneInfo(os.environ.get("ZONA_HORARIA") or "America/Argentina/Buenos_Aires")
DRY_RUN = (os.environ.get("DRY_RUN") or "false").lower() == "true"
DIAS_ACTIVOS = int(os.environ.get("DIAS_ACTIVOS") or "7")

VENTANA_PRIVADO = timedelta(days=7)
MAX_INTENTOS = 3
MAX_MENSAJE = 1000


def log(mensaje):
    print(mensaje, flush=True)


def aviso(mensaje):
    print(f"::warning::{mensaje}", flush=True)


def fallar(mensaje):
    print(f"::error::{mensaje}", flush=True)
    sys.exit(1)


# ---------------------------------------------------------------- HTTP

def llamar(metodo, url, token, params=None, json_body=None):
    headers = {"Authorization": f"Bearer {token}"}
    cuerpo = None

    if params and metodo == "GET":
        url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
    elif json_body is not None:
        cuerpo = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif params:
        cuerpo = urllib.parse.urlencode(params).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=cuerpo, headers=headers, method=metodo)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detalle = e.read().decode(errors="replace")
        raise RuntimeError(f"{metodo} {url.split('?')[0]} -> {e.code}: {detalle}") from None


def leer_paginado(ruta, token, params):
    url = f"{GRAPH}/{ruta}"
    resultado = []
    while url:
        datos = llamar("GET", url, token, params=params)
        resultado.extend(datos.get("data", []))
        url = datos.get("paging", {}).get("next")
        params = None  # el link "next" ya trae todos los parametros
    return resultado


# ---------------------------------------------------------------- Git

def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def guardar_estado(estado, mensaje):
    ARCHIVO_ESTADO.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVO_ESTADO.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if DRY_RUN:
        return
    git("add", str(ARCHIVO_ESTADO))
    if not git("status", "--porcelain", str(ARCHIVO_ESTADO)):
        return
    git("commit", "-m", mensaje)
    for intento in range(3):
        try:
            git("pull", "--rebase", "--autostash")
            git("push")
            return
        except subprocess.CalledProcessError as e:
            log(f"Push rechazado (intento {intento + 1}): {e.stderr.strip()}")
    aviso("No se pudo guardar el estado en el repo. En la proxima corrida se va a reintentar.")


# ---------------------------------------------------------------- respuesta.txt

def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if not unicodedata.combining(c))


def leer_respuesta(archivo):
    palabras, comentarios, mensaje = [], [], []
    en_mensaje = False

    for linea in archivo.read_text(encoding="utf-8").splitlines():
        if en_mensaje:
            mensaje.append(linea)
            continue
        clave, _, valor = linea.partition(":")
        clave = normalizar(clave.strip())
        valor = valor.strip()
        if clave == "palabra":
            palabras += [normalizar(p.strip()) for p in valor.split(",") if p.strip()]
        elif clave == "comentario" and valor:
            comentarios.append(valor)
        elif clave == "mensaje":
            en_mensaje = True
            if valor:
                mensaje.append(valor)

    texto_mensaje = "\n".join(mensaje).strip()
    if not palabras:
        raise ValueError("falta la linea PALABRA")
    if not comentarios and not texto_mensaje:
        raise ValueError("no tiene ni COMENTARIO ni MENSAJE, no hay nada para responder")
    if len(texto_mensaje) > MAX_MENSAJE:
        raise ValueError(f"el MENSAJE tiene {len(texto_mensaje)} caracteres y el limite es {MAX_MENSAJE}")

    patron = re.compile("|".join(rf"(?<!\w){re.escape(p)}(?!\w)" for p in palabras))
    return {"palabras": palabras, "patron": patron, "comentarios": comentarios, "mensaje": texto_mensaje}


def completar(plantilla, usuario):
    return plantilla.replace("{usuario}", f"@{usuario}" if usuario else "")


# ---------------------------------------------------------------- Posts activos

def posts_activos():
    if not CARPETA_BASE.is_dir():
        return []
    limite = datetime.now(ZONA) - timedelta(days=DIAS_ACTIVOS)
    activos = []

    for carpeta in sorted(CARPETA_BASE.iterdir()):
        marca = carpeta / "publicado.json"
        archivo = carpeta / "respuesta.txt"
        if not carpeta.is_dir() or not marca.exists() or not archivo.exists():
            continue

        publicado = json.loads(marca.read_text(encoding="utf-8"))
        cuando = datetime.fromisoformat(publicado["publicado_en"])
        if cuando < limite:
            continue

        try:
            config = leer_respuesta(archivo)
        except ValueError as e:
            aviso(f"{carpeta.name}/respuesta.txt: {e}. Se saltea este post.")
            continue

        caption = carpeta / "caption.txt"
        if caption.exists():
            texto = normalizar(caption.read_text(encoding="utf-8"))
            if not config["patron"].search(texto):
                aviso(f"{carpeta.name}: el caption no menciona la palabra {', '.join(config['palabras'])}")

        activos.append({"carpeta": carpeta.name, "media_id": publicado["media_id"], **config})
    return activos


# ---------------------------------------------------------------- Instagram

def resolver_pagina(ig_user, token):
    """El privado sale desde la pagina de Facebook vinculada y necesita su token."""
    token_pagina = os.environ.get("FB_PAGE_TOKEN") or ""
    if token_pagina:
        datos = llamar("GET", f"{GRAPH}/me", token_pagina, params={"fields": "id,name"})
        return datos["id"], token_pagina

    try:
        cuentas = leer_paginado(
            "me/accounts", token, {"fields": "id,name,access_token,instagram_business_account", "limit": 100}
        )
        for pagina in cuentas:
            if pagina.get("instagram_business_account", {}).get("id") == ig_user:
                return pagina["id"], pagina["access_token"]
    except RuntimeError as e:
        log(f"No se pudo leer me/accounts ({e}). Pruebo si el token ya es de pagina.")

    try:
        datos = llamar("GET", f"{GRAPH}/me", token, params={"fields": "id,instagram_business_account"})
        if datos.get("instagram_business_account", {}).get("id") == ig_user:
            return datos["id"], token
    except RuntimeError as e:
        log(f"Tampoco funciono como token de pagina: {e}")

    return None, None


def leer_comentarios(media_id, token):
    return leer_paginado(media_id + "/comments", token, {"fields": "id,text,username,timestamp", "limit": 50})


def responder_publico(comment_id, texto, token):
    return llamar("POST", f"{GRAPH}/{comment_id}/replies", token, params={"message": texto})["id"]


def responder_privado(page_id, comment_id, texto, token_pagina):
    cuerpo = {"recipient": {"comment_id": comment_id}, "message": {"text": texto}}
    return llamar("POST", f"{GRAPH}/{page_id}/messages", token_pagina, json_body=cuerpo)


def fecha_comentario(comentario):
    return datetime.strptime(comentario["timestamp"], "%Y-%m-%dT%H:%M:%S%z")


# ---------------------------------------------------------------- Principal

def procesar(post, comentario, registro, ig, ahora):
    """Devuelve True si cambio algo en el registro."""
    usuario = comentario.get("username") or ""

    quiere_privado = bool(post["mensaje"])
    if quiere_privado and not registro.get("privado"):
        if ahora - fecha_comentario(comentario) > VENTANA_PRIVADO:
            log(f"  @{usuario}: el comentario tiene mas de 7 dias, ya no se puede mandar privado")
            registro.update(listo=True, error="fuera del plazo de 7 dias")
            return True
        if not ig["page_id"]:
            registro["intentos"] = registro.get("intentos", 0) + 1
            registro["error"] = "no hay pagina de Facebook para mandar el privado"
            return True
        try:
            responder_privado(ig["page_id"], comentario["id"], completar(post["mensaje"], usuario), ig["token_pagina"])
            registro["privado"] = True
            log(f"  @{usuario}: privado enviado")
        except RuntimeError as e:
            registro["intentos"] = registro.get("intentos", 0) + 1
            registro["error"] = str(e)[:500]
            aviso(f"{post['carpeta']}: no se pudo mandar el privado a @{usuario}: {e}")
            return True

    # El comentario publico va despues del privado, asi no decimos "te lo mande" si fallo.
    if post["comentarios"] and not registro.get("publico"):
        try:
            texto = completar(random.choice(post["comentarios"]), usuario)
            registro["publico"] = responder_publico(comentario["id"], texto, ig["token"])
            log(f"  @{usuario}: respuesta publica -> {texto}")
        except RuntimeError as e:
            # Si el privado ya salio, no se reintenta: Instagram permite un solo privado por comentario.
            registro["error"] = str(e)[:500]
            aviso(f"{post['carpeta']}: no se pudo responder en publico a @{usuario}: {e}")
            if not registro.get("privado"):
                registro["intentos"] = registro.get("intentos", 0) + 1
                return True

    registro["listo"] = True
    if registro.get("publico") or not post["comentarios"]:
        registro.pop("error", None)
    return True


def main():
    activos = posts_activos()
    if not activos:
        print(f"::notice::No hay posts de los ultimos {DIAS_ACTIVOS} dias con respuesta.txt. Nada que revisar.")
        return

    ig_user = os.environ["IG_USER_ID"]
    token = os.environ["IG_ACCESS_TOKEN"]
    propio = llamar("GET", f"{GRAPH}/{ig_user}", token, params={"fields": "username"}).get("username", "")

    ig = {"token": token, "page_id": None, "token_pagina": None}
    if any(p["mensaje"] for p in activos):
        ig["page_id"], ig["token_pagina"] = resolver_pagina(ig_user, token)
        if ig["page_id"]:
            log(f"Privados desde la pagina {ig['page_id']}")
        else:
            aviso("No encontre la pagina de Facebook vinculada a la cuenta. Los privados no van a salir.")

    estado = json.loads(ARCHIVO_ESTADO.read_text(encoding="utf-8")) if ARCHIVO_ESTADO.exists() else {}
    ahora = datetime.now(timezone.utc)
    cambios = 0

    for post in activos:
        log(f"{post['carpeta']} | palabra: {', '.join(post['palabras'])}")
        try:
            comentarios = leer_comentarios(post["media_id"], token)
        except RuntimeError as e:
            aviso(f"{post['carpeta']}: no se pudieron leer los comentarios: {e}")
            continue

        coinciden = 0
        for comentario in comentarios:
            usuario = comentario.get("username") or ""
            if usuario.lower() == propio.lower():
                continue
            if not post["patron"].search(normalizar(comentario.get("text") or "")):
                continue
            coinciden += 1

            registro = estado.get(comentario["id"], {"post": post["carpeta"], "usuario": usuario})
            if registro.get("listo") or registro.get("intentos", 0) >= MAX_INTENTOS:
                continue

            if DRY_RUN:
                log(f"  @{usuario}: '{comentario.get('text')}' -> se responderia")
                continue

            if procesar(post, comentario, registro, ig, ahora):
                estado[comentario["id"]] = registro
                cambios += 1

        log(f"  {len(comentarios)} comentarios, {coinciden} con la palabra clave")

    if DRY_RUN:
        log("Modo prueba: no se respondio nada.")
        return
    if cambios:
        guardar_estado(estado, f"Respuestas a comentarios: {cambios}")
    log(f"Listo. Comentarios atendidos en esta corrida: {cambios}")


if __name__ == "__main__":
    main()
