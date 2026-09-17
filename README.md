# Recopilador y analizador de YouTube Shorts por temática

Dos etapas sobre un mismo corpus:

1. **Recopilar** — busca Shorts sobre una temática y los descarga en masa, junto con sus
   metadatos y subtítulos.
2. **Analizar** — los transcribe con un modelo de voz local y los clasifica con un modelo
   de lenguaje local, según las columnas que se definan, y lo vuelca todo a un CSV.

Las dos escriben en el mismo índice SQLite, así que el corpus y lo que se deduce de él no
pueden desincronizarse. La ventana tiene una pestaña por etapa.

Uso académico / de investigación (monitoría Colivri). La descarga masiva desde YouTube
contraviene sus Términos de Servicio; el recopilador limita la concurrencia e intercala
pausas para no golpear el servicio.

## Puesta en marcha

Requiere **Python 3.10 o superior**. En Python 3.9 la última versión instalable de yt-dlp
es de 2025 y YouTube ya la rechaza con *"The page needs to be reloaded"*.

```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
winget install --id Gyan.FFmpeg -e --scope user
winget install --id OpenJS.NodeJS.LTS -e --scope user
```

Para la etapa de análisis, además:

```bat
:: aceleración por GPU NVIDIA (opcional pero muy recomendable, ~700 MB)
.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt

:: modelo de lenguaje local para clasificar
winget install --id Ollama.Ollama -e
ollama pull qwen2.5:7b-instruct
```

Sin las DLL de CUDA la transcripción sigue funcionando, pero por CPU: se pasa de unos
segundos por Short a cerca de un minuto. Sin Ollama se puede transcribir igual, solo que
no se clasifica.

**ffmpeg es obligatorio**: YouTube entrega los Shorts con las pistas de video y audio
separadas y hay que unirlas. Si no está en el `PATH`, la app lo busca igualmente en la
carpeta donde lo deja winget.

**Un motor de JavaScript es casi obligatorio** (Node, o si se prefiere Deno o Bun). YouTube
protege las URLs de sus formatos con un reto en JavaScript; sin motor para resolverlo yt-dlp
cae a un modo degradado de un solo cliente de reproducción y falla muy a menudo con
*"The page needs to be reloaded"* o `HTTP 403`. yt-dlp solo activa Deno por su cuenta, así
que `config.py` detecta el que haya instalado y se lo declara.

## En Docker

Alternativa a la instalación de arriba para **el modo sin interfaz**. La imagen trae ya
Python 3.11, ffmpeg, Node y las libs de CUDA colocadas donde CTranslate2 las busca, que es
justo la parte que falla de formas distintas en cada equipo. La ventana gráfica no va en el
contenedor: se sigue abriendo nativa con `recopilador.bat`, contra el mismo corpus.

Antes del primer arranque hacen falta dos cosas en Windows:

1. **Docker Desktop con backend WSL2** y un driver NVIDIA reciente. El NVIDIA Container
   Toolkit ya viene dentro, no hay que instalar nada aparte. Se comprueba con:

   ```bat
   docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
   ```

2. **Compartir la carpeta del proyecto** en Docker Desktop → Settings → Resources → File
   Sharing. Está dentro de OneDrive, así que puede que no lo esté ya.

Ollama **no** hay que tocarlo. Aunque escuche solo en `127.0.0.1:11434`, Docker Desktop
enruta `host.docker.internal` hasta el loopback del host, y el contenedor ve los modelos ya
descargados sin abrir el puerto a la red. (En un Docker de Linux eso no vale: ahí sí haría
falta `OLLAMA_HOST=0.0.0.0:11434`.)

Después:

```bat
docker compose build
recopilador-docker.bat --cli --tema "pesca artesanal" --n 50
recopilador-docker.bat --analizar --exportar
recopilador-docker.bat --exportar-csv --tema "pesca artesanal"
```

`recopilador-docker.bat` es solo un atajo de `docker compose run --rm app`, y los argumentos
son los mismos que los de `main.py`. El primer `build` tarda unos diez minutos, y el primer
`--analizar` se baja además el modelo de voz (~1,5 GB), porque `modelos/` no va en el repo.

`data/` y `modelos/` se montan desde el disco, no viven dentro del contenedor: lo que
descarga el contenedor lo ve la ventana nativa y al revés, y borrar la imagen no se lleva ni
el corpus ni los pesos de Whisper.

**No conviene tener la ventana trabajando y el contenedor corriendo a la vez**: los dos
escriben en `index.sqlite`, y a través del montaje de Docker el bloqueo entre procesos no da
las garantías que sí da dentro de Windows.

De VRAM no sobra nada: `large-v3` en el contenedor (~1,6 GB) más `qwen2.5:7b` en el Ollama
del host (~4,7 GB) llenan los 6 GB de la tarjeta. Es lo mismo que ya ocurre sin Docker, pero
significa que no se puede levantar un segundo contenedor con GPU al lado.

### En un equipo sin tarjeta NVIDIA

El `docker-compose.yml` reserva la GPU, y esa reserva es dura: sin tarjeta no arranca **nada**,
ni siquiera la descarga, que no la necesita.

```
Error response from daemon: could not select device driver "nvidia" with capabilities: [[gpu]]
```

Hay que hacer dos cosas, y el orden da igual:

1. Borrar (o comentar) el bloque `deploy:` entero del `docker-compose.yml`.
2. Construir sin las libs de CUDA: `docker compose build --build-arg CON_GPU=0`.

No sirve intentar anularlo con un fichero de override: compose fusiona la lista de `devices`
en vez de reemplazarla, y la reserva sigue ahí. La transcripción irá entonces por CPU, del
orden de diez veces más lenta.

## Uso

Doble clic en `recopilador.bat`, o bien:

```bat
.venv\Scripts\python.exe main.py
```

**El doble clic en `main.py` también sirve**: Windows lo abriría con el lanzador `py.exe`, que
en muchos equipos resuelve a un Python viejo cuyo yt-dlp YouTube ya rechaza — la ventana abre,
la búsqueda encuentra vídeos y no se descarga ninguno. Para evitar esa trampa, `main.py` detecta
que no está corriendo en el `.venv` y **se relanza solo** con el intérprete del proyecto. Si el
`.venv` no existe o se ha quedado viejo, la ventana lo dice al abrir y deshabilita el botón, en
vez de terminar en un «0 descargados» sin explicación.

### Pestaña «Recopilar»

Se escribe el tema, la cantidad y se pulsa **Buscar y descargar**. La ventana muestra el
progreso vídeo a vídeo y **Cancelar** detiene el lote en pocos segundos sin dejar archivos
a medias.

### Pestaña «Analizar»

Trabaja sobre lo que ya está descargado. Se elige el alcance (*solo lo que falta* o *todo
el corpus*, opcionalmente filtrado por tema), se definen las columnas del dataset en
**Columnas del dataset...** y se pulsa **Transcribir y clasificar**. **Exportar CSV**
vuelca el resultado.

Que sea una pestaña aparte y no la cola de la descarga es deliberado: cambiar las columnas
y volver a clasificar cuesta un minuto, porque la transcripción ya está guardada y no hay
que repetirla. Ese ciclo corto es lo que permite iterar el esquema del dataset.

### Sin interfaz

```bat
.venv\Scripts\python.exe main.py --cli --tema "pesca artesanal" --n 50
.venv\Scripts\python.exe main.py --cli -p tiktok --tema "pesca artesanal" --n 50
.venv\Scripts\python.exe main.py --cli -p instagram --tema "pesca artesanal" --n 50
.venv\Scripts\python.exe main.py --analizar --exportar
.venv\Scripts\python.exe main.py --exportar-csv --tema "pesca artesanal"
```

Descarga: `--plataforma`/`-p` (`youtube`, `tiktok`, `instagram`), `--max-dur` (segundos, 180 por defecto), `--hilos`, `--carpeta`, `--sin-subs`,
`--incluir-horizontales`.

Análisis: `--esquema`, `--todos`, `--sin-clasificar`, `--retranscribir`, `--reclasificar`,
`--modelo-voz`, `--modelo-llm`, `--idioma`, `--exportar`.

## Qué queda en disco

```
data/
  videos/abc123.mp4        H.264 + AAC, 720 px de lado corto
  meta/abc123.info.json    ficha de YouTube, sin las listas de formatos
  subs/abc123.es.vtt       subtítulos automáticos, si YouTube los da
  index.sqlite             videos, busquedas + transcripciones, segmentos,
                           esquemas, clasificaciones
  archive.txt              control de descargas de yt-dlp
  dataset_*.csv            los datasets exportados
modelos/                   pesos de Whisper (large-v3 son ~1,5 GB)
```

Un Short ronda 0,5–1,5 MB, así que 100 vídeos ocupan unos 100 MB.

`modelos/` va fuera de `data/` a propósito: vaciar el corpus no debe obligar a volver a
descargar el modelo de voz.

Pedir dos veces el mismo tema **no repite vídeos**: los que ya están en el índice se
excluyen de la búsqueda, de modo que la segunda corrida amplía el corpus en vez de
duplicarlo.

Si se borran vídeos de `data/videos` a mano, el índice y `archive.txt` se ponen al día solos al
empezar la siguiente corrida: las filas cuyo archivo ya no existe se descartan y esos vídeos
vuelven a poder descargarse. Sin ese paso quedarían bloqueados para siempre, porque la búsqueda
los excluiría por estar en el índice y yt-dlp los saltaría por estar en `archive.txt`.

## Cómo encuentra los Shorts

La búsqueda de texto de YouTube casi no devuelve Shorts — se comprobó que tampoco con los
filtros `sp` de tipo o de duración. Los Shorts viven en superficies propias, y de ahí salen
los candidatos, en este orden:

1. **Páginas de hashtag**: `youtube.com/hashtag/<etiqueta>/shorts`. Es la fuente principal:
   todo lo que devuelve es un Short y es temático por construcción. Del tema se derivan
   varias etiquetas (`pesca artesanal` → `pescaartesanal`, `pesca`, `artesanal`).
2. **Pestañas `/shorts` de los canales** que aparecen al buscar el tema en texto, con un
   tope por canal para que el corpus no se llene de un solo autor.
3. **Resultados de texto**, verificando uno a uno contra `youtube.com/shorts/<id>`, que solo
   responde 200 si el vídeo es realmente un Short.

Con `YOUTUBE_API_KEY` en el `.env` se usa además la YouTube Data API v3 para descubrir
(100 unidades de cuota por búsqueda, ~100 búsquedas diarias en el plan gratuito). Si falta
la key, la librería o la cuota, cae solo a yt-dlp.

## TikTok e Instagram

Ninguna de las dos deja buscar a visitantes anónimos, así que antes de la primera corrida hay
que **iniciar sesión una vez** (botón «Iniciar sesión...» de la pestaña, o por consola):

```bat
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe main.py --login tiktok
.venv\Scripts\python.exe main.py --login instagram
```

Se abre un Chromium propio del proyecto; al detectar la sesión se guarda en
`data/.sesiones/` (perfil del navegador, `*_cookies.txt` para yt-dlp y la sesión de
instagrapi). **Esa carpeta equivale a una contraseña**: no la compartas. Usa cuentas
secundarias; ambas plataformas restringen las cuentas que hacen muchas peticiones
automáticas.

- **TikTok.** La búsqueda abre la web en ese navegador (`/search/video?q=` y `/tag/<etiqueta>`)
  y lee las respuestas de su API interna mientras hace scroll; de ahí salen también duración,
  dimensiones y vistas, y lo que no cumple los filtros se descarta sin bajarlo. La ventana se
  ve a propósito: si TikTok pide un captcha, lo resuelves tú y la búsqueda sigue (espera
  `Settings.espera_captcha` segundos). La descarga es la misma de YouTube, con yt-dlp.
- **Instagram.** Busca con [instagrapi](https://github.com/subzeroid/instagrapi): pestaña de
  Reels del buscador y luego hashtags. Descarga el mp4 directamente del CDN con la URL que da la
  búsqueda; si ha caducado, usa yt-dlp con las cookies de la sesión. En lugar de `--login` se
  puede poner `INSTAGRAM_SESSIONID` o usuario y contraseña en el `.env` (ver `.env.example`).
  Instagram no publica subtítulos.

Los archivos de estas plataformas llevan prefijo (`tiktok_<id>.mp4`,
`instagram_<shortcode>.mp4`); los de YouTube conservan el nombre `<id>.mp4` de siempre. El
CSV exportado incluye la columna `plataforma`.

## Estructura del código

| Archivo | Papel |
|---|---|
| `recopilador/config.py` | parámetros, rutas y localización de ffmpeg y del motor JS |
| `recopilador/entorno.py` | comprobación de Python, yt-dlp y ffmpeg antes de descargar |
| `recopilador/providers/` | un scraper por plataforma (`buscar` + `descargar`) y el navegador con sesión |
| `recopilador/search/` | backends de búsqueda y verificación de Shorts (YouTube) |
| `recopilador/downloader.py` | descarga de un vídeo, en dos fases |
| `recopilador/pipeline.py` | orquestación en paralelo y cancelación |
| `recopilador/store.py` | índice SQLite |
| `recopilador/ui/` | ventana, pestaña «Recopilar» y piezas comunes de la interfaz |
| `analisis/models.py` | `EsquemaDataset`: las columnas que define el usuario |
| `analisis/transcriptor.py` | faster-whisper |
| `analisis/clasificador.py` | cliente de Ollama y validación de la respuesta |
| `analisis/store.py` | tablas del análisis, sobre el mismo `index.sqlite` |
| `analisis/exportador.py` | volcado a CSV |
| `analisis/pipeline.py` | orquestación en serie y cancelación |
| `analisis/ui/` | pestaña «Analizar» y editor de columnas |

Dos decisiones que conviene no deshacer sin motivo:

- **La descarga va en dos fases.** Primero el vídeo y su ficha; después, aparte, los
  subtítulos. YouTube devuelve HTTP 429 en el endpoint de subtítulos con mucha facilidad, y
  en una sola fase ese 429 abortaba también la descarga del vídeo.
- **Los fallos pasajeros se reintentan en rondas.** YouTube corta las peticiones a rachas y
  puede tumbar un lote entero (403, 429, *"The page needs to be reloaded"*) aunque los vídeos
  estén disponibles: sin reintentos la corrida terminaba con cero descargas. Esos fallos no
  se indexan de inmediato; se reintentan hasta `Settings.reintentos` rondas, con pausa entre
  ellas y pidiendo clientes de reproducción alternativos, y solo cuentan como error si siguen
  fallando al final.
- **El límite de resolución se aplica al lado corto**, mediante `format_sort: res:N`. En un
  Short vertical la altura es el lado largo (1280, 1920), así que filtrar por `height<=720`
  dejaba fuera todo salvo los 360x640.

## Cómo se construye el dataset

Las columnas se definen desde la ventana: nombre, descripción y tipo (*categoría*,
*multietiqueta*, *booleano*, *número*, *texto*). De esa definición sale un **JSON Schema**
que se le pasa a Ollama en el campo `format`, de modo que el modelo no puede inventarse
columnas ni salirse de las opciones: la respuesta llega ya válida y no hay que parsear
texto libre ni reintentar por formato.

La descripción de cada columna **es parte del contrato**, no un comentario: es lo único que
el modelo lee para decidir qué poner ahí.

Detalles que conviene conocer antes de tocar nada:

- **Los valores se guardan como JSON** en `clasificaciones.campos_json`, no como columnas
  SQL. El esquema es editable, y hacer `ALTER TABLE` en cada cambio dejaría la base llena de
  columnas muertas. Así conviven varios esquemas sobre el mismo corpus —la clave es
  `(video_id, esquema)`— y el aplanado a columnas ocurre solo al exportar.
- **La evidencia son la transcripción y el título con sus hashtags.** En un corpus de
  Shorts la mayoría son gameplay sin voz —8 de 10 en el lote de LCK— y el título es lo único
  que hay. Clasificar solo por el audio dejaba casi todo el dataset vacío.
- **La columna `fuente_etiquetas` dice en qué se apoyó cada fila**: *transcripción*,
  *título y hashtags*, o *ambos*. Es lo que permite separar después lo que se oye de lo que
  el autor escribió para posicionar el vídeo; sin ella, mezclar las dos evidencias haría el
  dataset inservible para cualquier afirmación sobre el habla. **Es lo que el modelo declara
  haber usado, no una verificación independiente**: sirve para filtrar, no como prueba.
- **El nombre de columna `_fuente` está reservado** para ese campo, y el editor rechaza
  cualquier columna que empiece por guion bajo.
- **Temperatura 0 y semilla fija**: dos corridas sobre el mismo corpus dan el mismo
  dataset.
- **Volver a transcribir obliga a volver a clasificar**, aunque no se haya marcado la
  casilla. La clasificación anterior describía un texto que ya no existe, y guardarla junto
  a la transcripción nueva sería poner en el dataset una etiqueta que no corresponde a su
  propia fila.
- **Whisper transcribe todo**, también los vídeos que traen `.vtt` de YouTube. Los
  subtítulos automáticos no llevan puntuación y fallan con música o ruido; mezclar las dos
  fuentes daría un corpus con dos calidades de texto. Los `.vtt` quedan como referencia.
- **El VAD va activado.** Los Shorts llevan música y silencios, y ahí Whisper alucina
  frases repetidas hasta el final del audio.
- **El CSV se escribe en `utf-8-sig`**: sin el BOM, Excel en Windows abre el fichero con
  los acentos rotos.
- **La transcripción va en serie**, sin hilos: la GPU serializa el trabajo de todas formas
  y el paralelismo solo multiplicaría la memoria reservada en la tarjeta.
- **Al analizar se barren las filas huérfanas.** `recopilador.store.Store.reconciliar`
  quita del índice los vídeos cuyo `.mp4` desapareció, pero no sabe nada de las tablas del
  análisis. Sin `AnalisisStore.reconciliar`, vaciar `data/videos` dejaba la transcripción de
  un corpus que ya no existe y la pestaña decía «20 transcritos» con diez vídeos en disco.
- **Las DLL de CUDA se registran a mano** al cargar el modelo. `pip` las deja en
  `.venv\Lib\site-packages\nvidia\*\bin`, que no está en el `PATH`, y sin registrarlas el
  modelo llega a construirse en la GPU y revienta después, al codificar el primer audio,
  con *"Library cublas64_12.dll is not found"*. Por eso el respaldo a CPU cubre también el
  fallo al transcribir, y no solo el de carga.
