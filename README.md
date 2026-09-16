# Pichón

Un diario hablado que sale en papel.

De noche le cuentas al móvil cómo te ha ido el día. Un modelo de lenguaje
que corre en una Raspberry Pi —sin nube, sin API de nadie— lo destila en
un párrafo corto. Por la mañana, a las 9:00, un ESP32 despierta solo,
pide el resumen por WiFi y lo imprime en una térmica de 58 mm, junto con
una cita del libro de Lil B *Takin' Over by Imposing the Positive*.

```
  NOCHE                                    MAÑANA
  ┌─────────┐                              ┌─────────┐
  │  móvil  │  dictas tu día               │  ESP32  │  despierta a las 9:00
  └────┬────┘                              └────┬────┘
       │ HTTPS                                  │ HTTPS
       ▼                                        ▼
  ┌──────────────────────────────┐     ┌─────────────────┐
  │   Raspberry Pi 5             │     │  GET /ticket    │
  │   Flask + Ollama + harness   │────▶│  frase + día    │
  └──────────────────────────────┘     └────────┬────────┘
                                                │ serie 9600
                                                ▼
                                       ┌─────────────────┐
                                       │ térmica EM5820  │
                                       │  ticket de papel│
                                       └─────────────────┘
```

---

## Lo que este proyecto acabó siendo

Empezó como un cacharro de mesilla. Acabó siendo un caso de estudio sobre
**harness engineering**: rodear al modelo de maquinaria determinista
—preparar la entrada, puntuar y validar la salida, corregir con reglas,
tener un plan cuando falla— hasta que hace bien una tarea que no sabe
hacer solo.

El resultado corto, y es el dato que da sentido a todo lo demás:

> **Siete modelos probados, de 2B a 9B parámetros. Ninguno resolvió el
> problema. El harness sí — sobre el modelo de 3B, que es el más pequeño
> de los que redactan bien.**

La documentación completa de cómo se llegó ahí:

- **[docs/FASES.md](docs/FASES.md)** — el recorrido fase a fase: estado
  del sistema, qué falló, qué se cambió y qué resultado dio.
- **[docs/HARNESS.md](docs/HARNESS.md)** — la arquitectura del harness,
  con diagramas y el porqué de cada pieza.
- **[docs/MODELOS.md](docs/MODELOS.md)** — los siete modelos, con las
  salidas reales de cada uno sobre los mismos casos de prueba.

---

## Hardware

| Pieza | Detalle |
|---|---|
| Raspberry Pi 5 | 8 GB RAM, Raspberry Pi OS, hostname `pichon` |
| ESP32 | DOIT DEVKIT V1 (WROOM, CP2102) |
| Impresora | EM5820 térmica, 58 mm, TTL 9600 baudios |
| Móvil | cualquiera con Chrome, para dictar |

**Cableado** (tres cables, nada más):

```
ESP32 GPIO17 (TX2) ──────▶ pin TX de la impresora   TX, no RX
ESP32 GND ───────────────▶ GND de la impresora
ESP32 GND ───────────────▶ CTS de la impresora      (control de flujo)
```

La impresora lleva **fuente propia**: tira picos de 1,5-2 A al imprimir
y reiniciaría el ESP32 si colgara de él.

---

## Software

| Archivo | Qué es |
|---|---|
| `pichon_servidor.py` | El cerebro. Flask + Ollama + todo el harness |
| `pichon_esp32_final/pichon_esp32_final.ino` | El sketch del ESP32 |
| `pichon_esp32_final/credenciales_ejemplo.h` | Plantilla de WiFi — **hay que copiarla** |
| `pichon_frases.txt` | 109 citas del libro, verificadas literales |
| `vocabulario_ejemplo.py` | Tus nombres propios para Whisper — **cópialo** |

### 1. La Raspberry Pi

```bash
sudo apt install python3-flask python3-requests ffmpeg -y
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b            # redacta
ollama pull qwen2.5:7b-instruct    # verifica

# Whisper para transcribir (ffmpeg convierte el audio del navegador)
git clone https://github.com/ggerganov/whisper.cpp ~/whisper.cpp
cd ~/whisper.cpp && cmake -B build && cmake --build build -j4
bash ./models/download-ggml-model.sh small

scp pichon_servidor.py pichon_frases.txt usuario@pichon.local:~/
```

```bash
cp vocabulario_ejemplo.py vocabulario.py   # y pon tus nombres propios
```

Ese archivo le dice a Whisper qué nombres esperar: tu ciudad, tu calle,
la gente con la que hablas. Sin él funciona igual, pero los nombres
propios poco frecuentes salen destrozados — "Zaragoza" se convertía en
"Sara Goza" hasta que se añadió. No se sube al repo.

El diario (`pichon_diario.json`) se crea solo con la primera entrada.
Si `whisper.cpp` no está, el servidor lo detecta y usa la transcripción
del navegador: funciona igual, solo que peor.

Como servicio (arranca solo, sobrevive a reinicios):

```ini
# /etc/systemd/system/pichon.service
[Unit]
Description=Pichon - diario nocturno
After=network-online.target ollama.service
Requires=ollama.service

[Service]
ExecStart=/usr/bin/python3 /home/usuario/pichon_servidor.py https
WorkingDirectory=/home/usuario
User=usuario
Restart=always

[Install]
WantedBy=multi-user.target
```

### 2. El ESP32

```bash
cd pichon_esp32_final
cp credenciales_ejemplo.h credenciales.h   # y rellénalo con tu WiFi
```

En el IDE de Arduino: placa **ESP32 Dev Module** (o DOIT ESP32 DEVKIT
V1), y subir. Pulsar RESET imprime al momento, sin esperar a las 9:00.

> **La ruta no puede tener paréntesis ni espacios.** Las herramientas
> de compilación de ESP32 fallan con un error confuso sobre
> `bootloader.bin`.
>
> Aviso: El ESP32 clásico **solo ve WiFi de 2,4 GHz**.

### Endpoints

| Ruta | Quién la usa | Qué hace |
|---|---|---|
| `GET /` | el móvil | la web para dictar |
| `POST /contar` | el móvil | destila el día y lo guarda |
| `GET /ticket` | el ESP32 | frase + resumen, **consume** el resumen |
| `GET /previsualizar` | el móvil | lo mismo, sin consumir |
| `GET /historial` | el móvil | entradas anteriores |

**`POST /contar`** es `multipart/form-data` con dos campos:
`texto` (lo que entendió el navegador) y `audio` (opcional, el dictado
en crudo). Si hay audio y Whisper está disponible, gana la transcripción
de Whisper.

**`GET /ticket`** devuelve texto plano, sin tildes (la térmica usa otra
tabla de caracteres):

```
FRASE:Think about ten things you like about yourself...
MORALEJA:Hoy jugaste al ajedrez con tu padre y el te gano dos partidas.
FIN:
```

La línea `MORALEJA:` solo aparece si dictaste en las últimas
**18 horas** (`VENTANA_HORAS`). Pasado ese plazo sale solo la frase —
es deliberado: no quieres que el ticket del jueves imprima el lunes. Es
también la causa más probable de un ticket "vacío".

En el código el resumen se llama **moraleja** en todas partes: el campo
del JSON, la variable y el protocolo del ticket.

---

## Dos decisiones que merecen explicación

**La RAM vuelve a cero entre peticiones.** La Pi se comparte con otros
proyectos, así que ningún modelo queda residente: se carga el de 3B, se
suelta, se carga el de 7B si hace falta, se suelta. Nunca coexisten.
Cuesta ~30 s de carga por petición y deja 7,5 GB libres el resto del día.

**El verificador solo entra cuando hace falta.** Puntuar un párrafo es
determinista y gratis; verificarlo frase a frase cuesta ~20 s por frase.
Si la nota pasa de 65 no se verifica. Los días claros tardan ~40 s; los
ambiguos, ~160 s.

---

## Estado

**Funciona de extremo a extremo**: dictas de noche, imprime a las 9:00.

**Transcripción con Whisper.** El navegador graba el audio además de
usar el reconocimiento de Chrome, y la Pi lo pasa por `whisper.cpp`
(modelo `small`, más rápido que tiempo real: 11 s de audio en 7,4 s).
Si Whisper falla o no está, se usa el texto de Chrome automáticamente.

La diferencia es grande: donde Chrome partía un nombre propio en dos
palabras sin sentido, Whisper lo transcribe entero.

### Lo que está medido

Sobre **el mismo dictado repetido 20 veces**: 20/20 sin fallos, 3,5 cm
de papel, 288-301 caracteres, 38 s de media.

Sobre **ocho días que el sistema no había visto**: **4 de 8 con algún
fallo** — pierde algún detalle, se le cuela la primera persona en
presente (*"necesito operarme"*), alguna frase con gramática rota.

Esa diferencia es el dato honesto del proyecto: **el sistema está
ajustado a los días con los que se desarrolló**. No inventa días enteros
ni invierte quién gana a quién —los fallos graves están resueltos— pero
generalizar sigue abierto. Ver
[HARNESS.md § Lo que el harness NO arregla](docs/HARNESS.md).

### Coste por ticket

| | |
|---|---|
| Papel | 3,5 cm (10 líneas) |
| Tiempo | 38 s de media; 25 s si el 3B acierta a la primera, ~100 s si entra el 7B |
| RAM en reposo | 0 — ningún modelo queda cargado |

Un rollo de 58 mm estándar da para unos 270 tickets: nueve meses.

---

## Créditos y licencia

Las 109 frases de `pichon_frases.txt` son citas literales de
**Brandon "Lil B" McCartney**, *Takin' Over by Imposing the Positive*
(2012). Se incluyen como citas para uso personal, con atribución. Si
reutilizas este proyecto, plantéate poner tus propias frases: el
formato es una por línea, y las que empiezan por `#` se ignoran.

El código es tuyo para lo que quieras.
