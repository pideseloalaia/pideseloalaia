# Respuestas automáticas por palabra clave

Cada post tiene su propia palabra clave. Cuando alguien la comenta, el workflow le manda un mensaje privado y le responde el comentario en público.

## Cómo se usa cada día

En la carpeta del post, junto al `caption.txt` y las imágenes, agregá un `respuesta.txt` (hay un modelo en `contenido/_plantilla/`):

```
contenido/2026-11-24_5-recetas-con-ia/
  caption.txt
  respuesta.txt
  5recetas-1.png
  ...
```

```
PALABRA: RECETA
COMENTARIO: Listo {usuario}, te lo mandé por privado.
COMENTARIO: {usuario} ya lo tenés en tus mensajes.
MENSAJE:
Hola {usuario}, gracias por comentar.
Acá tenés la guía: https://...
```

- **PALABRA:** la que tienen que comentar. Podés poner varias separadas por coma (`RECETA, RECETAS`). No importan mayúsculas ni tildes, pero tiene que ser la palabra completa: si la palabra es RECETA, "recetario" no cuenta.
- **COMENTARIO:** la respuesta pública. Poné dos o tres distintas: se elige una al azar, porque si Instagram ve la misma respuesta repetida muchas veces puede tomarla como spam.
- **MENSAJE:** el privado. Va desde la línea siguiente hasta el final del archivo, con un máximo de 1000 caracteres.
- `{usuario}` se reemplaza por el @ de la persona.
- Si no ponés MENSAJE, solo se responde en público.

Acordate de nombrar la palabra en el `caption.txt` ("Comentá RECETA y te mando la guía"). Si no aparece, el log te avisa.

Se revisan los posts de los últimos 7 días. Pasado ese plazo Instagram ya no deja mandar el privado.

## Configuración (una sola vez)

### 1. Permisos del token

El token que ya tenés en `IG_TOKEN` sirve para publicar, pero para leer comentarios y mandar privados necesita más permisos. En el [Explorador de la API Graph](https://developers.facebook.com/tools/explorer/) generá uno nuevo con:

- `instagram_basic`
- `instagram_content_publish`
- `instagram_manage_comments`
- `instagram_manage_messages`
- `pages_show_list`
- `pages_read_engagement`
- `pages_manage_metadata`
- `pages_messaging`

Pasalo a token de larga duración como hiciste la primera vez y actualizá el secret `IG_TOKEN`. El script busca solo la página de Facebook vinculada y saca de ahí el token de página para los privados.

Si preferís cargar el token de página aparte, guardalo en un secret `FB_PAGE_TOKEN` y el script usa ese.

### 2. Permitir el acceso a mensajes en Instagram

En la app de Instagram, en la cuenta de Pídeselo a la IA: **Configuración → Mensajes y respuestas a historias → Controles de mensajes → Herramientas conectadas → Permitir acceso a los mensajes**. Sin eso, los privados fallan.

### 3. Revisión de la app (para los privados)

Mientras la app de Meta esté en modo desarrollo, los privados solo les llegan a personas que tienen un rol en la app (administrador, desarrollador o evaluador). La respuesta pública sí funciona con cualquiera.

Para mandarle privados a cualquier seguidor hay que pedir **acceso avanzado** a `instagram_manage_messages` y `pages_messaging` en la revisión de la app, y pasarla a modo publicado. Meta suele pedir un video corto que muestre el uso.

Mientras tanto podés dejar `respuesta.txt` sin MENSAJE y usar solo la respuesta pública (por ejemplo, "El link está en la bio").

### 4. Nuevo job en cron-job.org

Igual al de publicar, cambiando solo el nombre del workflow en la URL:

- **URL:** `https://api.github.com/repos/pideseloalaia/pideseloalaia/actions/workflows/responder-comentarios.yml/dispatches`
- **Método:** `POST`
- **Headers:** los mismos cuatro del job de publicar.
- **Body:** `{"ref":"main"}`
- **Frecuencia:** cada 10 minutos. Más seguido no hace falta y llena el historial de Acciones.

El token de GitHub que ya usa cron-job.org sirve, porque tiene permiso de Actions sobre el repo.

## Probar

1. Comentá la palabra desde otra cuenta en un post que tenga `respuesta.txt`.
2. **Acciones → Responder comentarios → Ejecutar flujo de trabajo** con **dry_run** marcado. El log muestra a quién respondería.
3. Corrélo sin dry_run y fijate que llegue el privado y aparezca la respuesta.

## Cómo evita repetir

Lo que ya se respondió queda en `estado/respondidos.json`. Si un privado falla, se reintenta hasta 3 veces en las corridas siguientes y el error queda anotado en ese archivo. La respuesta pública sale después del privado, así no queda escrito "te lo mandé" si el privado no salió.

Para volver a responderle a alguien, borrá su entrada de `estado/respondidos.json`.

## Errores comunes

- **Error 10 o 200 al mandar el privado:** falta un permiso del paso 1, o la persona no tiene rol en la app (paso 3).
- **Error 551 o "user cannot be messaged":** no está activado el acceso a mensajes del paso 2, o esa persona no acepta mensajes.
- **"No encontré la página de Facebook vinculada":** el token no tiene `pages_show_list`, o la cuenta de Instagram no está vinculada a una página.
- **No lee comentarios:** falta `instagram_manage_comments`.
