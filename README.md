# π-chón

A spoken diary that comes out on paper.

> The name is written **π-chón** — the pi from Raspberry Pi. GitHub only
> accepts ASCII in repository names, so there it stays `pichon-diario`.

At night you tell your phone how your day went. A language model running
on a Raspberry Pi — no cloud, nobody's API — distills it into a short
paragraph. In the morning, at 9:00, an ESP32 wakes up on its own, asks
for the summary over WiFi and prints it on a 58 mm thermal printer,
along with a quote from Lil B's *Takin' Over by Imposing the Positive*.

```
  NIGHT                                    MORNING
  ┌─────────┐                              ┌─────────┐
  │  phone  │  you dictate your day        │  ESP32  │  wakes at 9:00
  └────┬────┘                              └────┬────┘
       │ HTTPS                                  │ HTTPS
       ▼                                        ▼
  ┌──────────────────────────────┐     ┌─────────────────┐
  │   Raspberry Pi 5             │     │  GET /ticket    │
  │   Flask + Ollama + harness   │────▶│  quote + day    │
  └──────────────────────────────┘     └────────┬────────┘
                                                │ serial 9600
                                                ▼
                                       ┌─────────────────┐
                                       │ EM5820 thermal  │
                                       │  paper ticket   │
                                       └─────────────────┘
```

---

## What this project turned into

It started as a bedside gadget. It ended up a case study in **harness
engineering**: wrapping the model in deterministic machinery — preparing
the input, scoring and validating the output, correcting with rules,
having a plan for when it fails — until it does well a task it cannot do
on its own.

The short version, and the number that gives everything else meaning:

> **Seven models tested, from 2B to 9B parameters. None of them solved
> the problem. The harness did — on the 3B model, the smallest one that
> writes decent Spanish.**

Full documentation of how we got there:

- **[docs/PHASES.md](docs/PHASES.md)** — the journey phase by phase:
  system state, what broke, what changed, what it measured.
- **[docs/HARNESS.md](docs/HARNESS.md)** — the harness architecture,
  with diagrams and the reasoning behind each piece.
- **[docs/MODELS.md](docs/MODELS.md)** — the seven models, with real
  outputs from each on the same test cases.

---

## Hardware

| Part | Detail |
|---|---|
| Raspberry Pi 5 | 8 GB RAM, Raspberry Pi OS, hostname `pichon` |
| ESP32 | DOIT DEVKIT V1 (WROOM, CP2102) |
| Printer | EM5820 thermal, 58 mm, TTL 9600 baud |
| Phone | any with Chrome, for dictating |

**Wiring** (three wires, that's all):

```
ESP32 GPIO17 (TX2) ──────▶ printer TX pin        TX, not RX
ESP32 GND ───────────────▶ printer GND
ESP32 GND ───────────────▶ printer CTS           (flow control)
```

The printer runs on **its own power supply**: it draws 1.5-2 A spikes
while printing and would reset the ESP32 if it hung off it.

All the wiring is **hand-soldered**, point by point: the three wires to
the printer header, the CTS-to-ground jumper and the power. No
breadboard, no dupont connectors — a device that lives on a nightstand
and gets plugged and unplugged eventually shakes a wire loose, and a bad
contact on the serial line doesn't fail cleanly: it shows up as garbage
characters mid-line, which is far more annoying to diagnose than a wire
that simply doesn't connect.

![The build: ESP32, thermal printer and the hand-soldered wiring](fotos/montaje.jpg)

---

## Software

> The code is written in Spanish — function names, constants and
> comments. This documentation refers to them as they are
> (`destilar()`, `LARGO_TICKET`).

| File | What it is |
|---|---|
| `pichon_servidor.py` | The brain. Flask + Ollama + the whole harness |
| `pichon_esp32_final/pichon_esp32_final.ino` | The ESP32 sketch |
| `pichon_esp32_final/credenciales_ejemplo.h` | WiFi template — **copy it** |
| `vocabulario_ejemplo.py` | Your proper nouns for Whisper — **copy it** |
| `pichon_frases.txt` | 109 quotes from the book, verified literal |

### 1. The Raspberry Pi

```bash
sudo apt install python3-flask python3-requests ffmpeg -y
curl -fsSL https://ollama.com/install.sh | sh
ollama pull llama3.2:3b            # writes
ollama pull qwen2.5:7b-instruct    # judges

# Whisper for transcription (ffmpeg converts the browser's audio)
git clone https://github.com/ggerganov/whisper.cpp ~/whisper.cpp
cd ~/whisper.cpp && cmake -B build && cmake --build build -j4
bash ./models/download-ggml-model.sh small

scp pichon_servidor.py pichon_frases.txt user@pichon.local:~/
```

```bash
cp vocabulario_ejemplo.py vocabulario.py   # then add your proper nouns
```

That file tells Whisper which names to expect: your city, your street,
the people you talk about. Without it everything still works, but
uncommon proper nouns come out mangled — one city name kept turning into
two unrelated words until it was added. It never reaches the repo.

The diary (`pichon_diario.json`) is created on the first entry.

As a service (starts on boot, survives reboots):

```ini
# /etc/systemd/system/pichon.service
[Unit]
Description=Pichon - nightly diary
After=network-online.target ollama.service
Requires=ollama.service

[Service]
ExecStart=/usr/bin/python3 /home/user/pichon_servidor.py https
WorkingDirectory=/home/user
User=user
Restart=always

[Install]
WantedBy=multi-user.target
```

### 2. The ESP32

```bash
cd pichon_esp32_final
cp credenciales_ejemplo.h credenciales.h   # then fill in your WiFi
```

In the Arduino IDE: board **ESP32 Dev Module** (or DOIT ESP32 DEVKIT
V1), then upload. Pressing RESET prints immediately, without waiting for
9:00.

> **The path cannot contain parentheses or spaces.** The ESP32 build
> tools fail with a confusing error about `bootloader.bin`.
>
> The classic ESP32 **only sees 2.4 GHz WiFi**.

### Endpoints

| Route | Who calls it | What it does |
|---|---|---|
| `GET /` | the phone | the dictation page |
| `POST /contar` | the phone | distills the day and stores it |
| `GET /ticket` | the ESP32 | quote + summary, **consumes** the summary |
| `GET /previsualizar` | the phone | same thing, without consuming |
| `GET /historial` | the phone | previous entries |

**`POST /contar`** is `multipart/form-data` with two fields: `texto`
(what the browser heard) and `audio` (optional, the raw dictation). If
there is audio and Whisper is available, Whisper's transcription wins.

**`GET /ticket`** returns plain text without accents (the thermal
printer uses a different character table):

```
FRASE:Think about ten things you like about yourself...
MORALEJA:Hoy jugaste al ajedrez con tu padre y el te gano dos partidas.
PROYECTOS:EN MARCHA:
- Tirar curriculum
- Cyberdeck PSP
FIN:
```

The `MORALEJA:` line only appears if you dictated within the last
**18 hours** (`VENTANA_HORAS`). After that only the quote comes out —
deliberately: you don't want Thursday's ticket printing on Monday. It is
also the most likely cause of an apparently "empty" ticket.

`PROYECTOS:` is a running list of what you have in progress. It always
prints, and it updates by voice: *"add X to the list"*, *"I finished
X"*. Only explicit phrases change it — merely mentioning a project never
removes it.

In the code the summary is called **moraleja** everywhere: the JSON
field, the variable and the ticket protocol.

---

## Two decisions worth explaining

**RAM goes back to zero between requests.** The Pi is shared with other
projects, so no model stays resident: the 3B loads, gets released, the
7B loads if needed, gets released. They never coexist. It costs ~30 s of
loading per request and leaves 7.5 GB free the rest of the day.

**The judge only steps in when needed.** Scoring a paragraph is
deterministic and free; verifying it sentence by sentence costs ~20 s
per sentence. Above a score of 65 it isn't verified.

---

## Status

**Works end to end**: you dictate at night, it prints at 9:00.

**Whisper transcription.** The browser records audio alongside Chrome's
speech recognition, and the Pi runs it through `whisper.cpp` (`small`
model, faster than real time: 11 s of audio in 7.4 s). If Whisper fails
or isn't installed, Chrome's text is used automatically.

The difference is large: where Chrome split a proper noun into two
meaningless words, Whisper transcribes it whole.

### What has been measured

On **the same dictation repeated 20 times**: 20/20 with no failures,
3.5 cm of paper, 288-301 characters, 38 s average.

On **eight days the system had never seen**: **4 out of 8 had some
failure** — drops a detail, lets first person slip through in the
present tense, an occasional broken sentence.

That gap is the honest number of this project: **the system is
overfitted to the days it was developed against**. It doesn't invent
whole days or flip who won a game — the serious failures are fixed — but
generalizing is still open. See
[HARNESS.md § What the harness does NOT fix](docs/HARNESS.md).

### Cost per ticket

| | |
|---|---|
| Paper | 3.5 cm (10 lines), ~6 cm with the project list |
| Time | 38 s average; 25 s if the 3B nails it first try, ~100 s if the 7B steps in |
| RAM at rest | 0 — no model stays loaded |

A standard 58 mm roll gives around 150 tickets: five months.

---

## Credits and license

The 109 quotes in `pichon_frases.txt` are literal excerpts from
**Brandon "Lil B" McCartney**, *Takin' Over by Imposing the Positive*
(2012). They are included as quotations for personal use, with
attribution. If you reuse this project, consider using your own lines:
the format is one per line, and lines starting with `#` are ignored.

The code is yours to do whatever you want with.
