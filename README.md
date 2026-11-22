# Pídeselo a la IA: publicación automática

Todos los días, cron-job.org dispara un workflow de GitHub. El workflow busca en `contenido/` la carpeta con la fecha de hoy (hora de Argentina) y la publica en Instagram.

## Cómo subir una publicación

1. En el repo, entrá a `contenido/`.
2. Creá una carpeta cuyo nombre **empiece con la fecha** de publicación. Después de la fecha podés agregar lo que quieras:
   ```
   contenido/2026-11-24_5-recetas-con-ia/
   ```
3. Adentro poné:
   - `caption.txt` con el texto del post (copiá el de `contenido/_plantilla/`).
   - Las imágenes, numeradas en el orden del carrusel: `5recetas-1.png`, `5recetas-2.png`, ...
   - O un solo `.mp4` si ese día va un Reel.

| Qué hay en la carpeta | Qué se publica |
|---|---|
| 1 imagen | Post simple |
| 2 a 10 imágenes o videos | Carrusel, en orden por número |
| 1 video | Reel (también aparece en el feed) |

Algunos detalles:
- Los PNG de Gemini se convierten solos a JPEG.
- Podés cargar varios días por adelantado. Cada día sale solo lo suyo.
- Si un día no tiene carpeta, no se publica nada y no da error.
- Después de publicar aparece `publicado.json` en la carpeta. Eso evita que el post se repita.
- Tiene que haber **una sola carpeta por fecha**.

## Problemas comunes

Para ver qué pasó en una corrida, entrá a la pestaña **Acciones** y abrí la última.

- **Error 9004 o "Only photo or video can be accepted":** Instagram no pudo bajar la imagen. Revisá que el repo siga siendo público.
- **Error 190:** venció el token de Instagram. Actualizá el secret `IG_ACCESS_TOKEN`.
- **cron-job.org marca 404:** el token de GitHub no tiene permiso de Actions o venció.
- **cron-job.org marca 422:** el body no dice `"ref":"principal"`.

Para republicar un día: borrá su `publicado.json` y corré el workflow a mano con esa fecha.
