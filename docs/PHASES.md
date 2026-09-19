# The journey, phase by phase

Each phase documents three things: **what state the system was in**, **what
failed**, and **what was changed**. The outputs shown are real, copied
straight from the tests.

The test case that runs through this whole document is this dictation, with
two traps set on purpose — filler words and an inversion of agency (the
other person wins, not the narrator):

> *"pues hoy eh me levante tarde o sea fatal y luego jugue al tenis con
> Clara y me ha ganado ella 6-3 sabes, estuve toda la tarde con el
> conector de la impresora que estaba mal puesto al final lo saque estoy
> contento pero agotado"*

---

## Phase 1 — Infrastructure

**State:** the LLM ran on an Android tablet with Termux, 4 GB of RAM. The
Raspberry Pi was unconfigured.

**Problem:** 4 GB isn't enough for a decent model, and the tablet had to
be switched on and awake all night.

**What was done:** move everything to the Pi 5 (8 GB). SSH by key, Ollama
installed, Flask server deployed.

**Result:** the cycle works, but the Pi lived off the phone's hotspot and
the server was launched with `nohup` — it died on every reboot.

---

## Phase 2 — The two networks and automatic startup

**State:** Pichón worked as long as nobody touched anything.

**Problems found:**

1. The Pi only knew the phone's hotspot. In the morning, with the hotspot
   off, there was no ticket.
2. `nohup` doesn't survive a reboot.
3. The ESP32 pointed at a fixed IP, and the IP changes depending on the
   network.

**What was done:**

- Two network profiles with priority (home 10, hotspot 5). The Pi latches
  onto whichever it finds.
- **mDNS**: the Pi is called `pichon` and answers to `pichon.local`. The
  ESP32 asks by name, so the IP doesn't matter.
- `systemd` with `After=ollama.service` and `Restart=always`.

**A trap that cost a whole session:** connecting the Pi to the home
network over SSH cuts your own session. The fix was a transient service
with `systemd-run` that attempts the connection, waits, and falls back to
the hotspot no matter what.

**Result:** cold reboot verified. The Pi boots on its own, connects on its
own, and the server comes up on its own.

---

## Phase 3 — The quotes from the book

**State:** the quotes file held 181 fragments chopped out of the book,
unverified.

**Problem:** the fragments had lost their meaning. *"That stops the
positive."* or *"Life is real."* aren't quotes, they're pieces of longer
sentences. The book has a voice; the chopping killed it.

**What was done:** read the whole PDF (196 pages, 100,561 characters),
extract 109 complete quotes that stand on their own, and **verify each one
literally against the original**. The verifier found 10 non-literal ones on
the first pass; they were fixed one by one.

**Result:** 109 quotes, 0 non-literal. The rotation goes through them
without repeating for 109 days.

```
  I knew a guy once who was down because he didn't have any friends.
  I took him to the book store and told him to look around --
  he had thousand of good friends on the shelves.
```

*(`thousand` is a typo in the original book; it's kept, these are exact
quotes.)*

---

## Phase 4 — The ESP32 prints

**State:** server working, ESP32 with a sketch that pointed at a fixed IP
and expected hardware that didn't exist.

**What changed in the sketch:**

| | Before | Now |
|---|---|---|
| Networks | one, left blank | two, with `WiFiMulti` |
| Destination | fixed IP | `pichon.local` + fallback IPs |
| Protocol | HTTP | HTTPS with `setInsecure()` |
| Power-on | MOSFET on GPIO25 | nothing: its own supply |
| Test | button on GPIO33 | the ESP32's RESET |

**A detail that would have broken the build:** the sketch enabled
`ext0_wakeup` on GPIO33. With no button wired up, that pin floats and can
wake the ESP32 by itself, printing tickets at odd hours.

**Result:** it prints.

**About the hardware:** the build is hand-soldered, not assembled on a
breadboard. In a device that lives on the bedside table, that gets plugged
and unplugged, a loose dupont wire eventually happens — and a bad contact
on the serial line doesn't fail cleanly: it shows up as garbage characters
halfway through a line, which is far harder to diagnose than a cable that
simply doesn't connect.

---

## Phase 5 — The first real failure: hallucinating sports

**State:** the whole circuit works. The first real dictation arrives, about
work, the paperwork, and returning the keys.

**What it printed:**

> *"Hoy jugaste al tenis, hoy jugaste al fútbol, hoy jugaste al voleibol."*

There wasn't a single sport in the dictation. And the reminder the user
explicitly asked for — returning the keys — vanished.

**The cause, and it's instructive:** the prompt contained this rule:

```
- Habla en segunda persona: "hoy jugaste al tenis...".
```

The 3B model **copied the example** instead of applying it, and then
followed the pattern by inventing two more sports. The file itself already
warned about this — "the example must teach the FORMAT, never the content"
— but the rule had been written with a concrete example.

**What was done:**

1. Rewrite the rule without examples containing real content.
2. Add `validar_directo()`: it rejects repeated sentences and outputs whose
   vocabulary doesn't come from the original text.
3. A new rule: *"If they asked you to remember something for tomorrow, that
   ALWAYS goes in the paragraph"*.

**Result:**

> *"Hoy has estado bien concentrado en el trabajo... No olvides recordar
> la el papeleo y la suerte para el domingo en la iglesia de Santo
> Domingo, y recuérdame que lo más importante para hoy es mandar
> las llaves."*

---

## Phase 6 — Who beats whom

**State:** it no longer invents activities. But a subtler failure shows up.

**The problem:** with the dictation *"jugué al ajedrez con mi padre y me ganó
dos partidas"*, the model wrote:

> *"jugaste al ajedrez con tu padre y **te ganaste** dos partidas"*

It inverts the result, or leaves it meaningless. **4 out of 4 attempts
failed.**

**What did NOT work** — six prompt variants, all tested. The three most
representative:

| Variant | Result |
|---|---|
| Narrow rule ("what OTHER people did goes in third person") | *"le ganaste"* — inverts it all the same |
| Explicit rule about winning/losing | *"te ganaste"* — no change |
| "Always name the subject" | *"tu padre jugaste al ajedrez contigo"* — ungrammatical |

**The discovery:** when the subject is **explicit and up front**, the model
always gets it right. *"ella te ganó 6-3"*, correct in every test. It only
fails when the subject is implicit.

**What was done** — attack from both sides, without touching the model:

- `explicitar_sujeto()` rewrites the **input** before the model reads it:
  *"me ganó dos partidas"* → *"mi padre me ganó dos partidas"*.
- `arreglar_concordancia()` fixes the **output**: *"él te ganaste"* is
  ungrammatical in Spanish (third-person subject with a second-person
  verb), so when it appears we know for certain the verb is wrongly
  conjugated, and we move it to third person.

**Result:** 4 out of 4 correct.

> *"Hoy por la tarde jugaste al ajedrez con tu padre y **tu padre te ganó**
> dos partidas seguidas, luego tu hermana preparó la cena."*

---

## Phase 7 — What if we try a bigger model?

**The reasonable hypothesis:** a 3B falls short; with 7B, 8B or 9B this
fixes itself.

**It did not fix itself.** The seven models on the same chess case (eight
rows: the 3B appears both with and without the harness):

| Model | Does it get who wins right? | Does it make things up? |
|---|---|---|
| `llama3.2:3b` **+ harness** | yes, 4/4 | no |
| `llama3.2:3b` alone | fails | no |
| `qwen2.5:7b-instruct` | fails, copies the original | doesn't rewrite |
| `llama3.1:8b` | inverts the result | yes: *"traer tus libros"*, *"estudiar para el examen"* |
| `mistral:7b` | inverts the result | yes: confuses verb tenses |
| `glm4:9b` | fails | — (322 s per response) |
| `gemma2:2b` | fails | yes: *"te sentaste bien"* |
| `phi3:3.8b` | inverts the result | broken Spanish: *"la concentra extranjera"* |

Full detail in [MODELS.md](MODELS.md).

**The conclusion that changed the project's direction:**

> More parameters don't fix the problem. The harness does. And the 3B, on
> top of getting it right with help, is **3 times faster** than the 8Bs.

---

## Phase 8 — Harness engineering

**State:** the 3B with two deterministic correctors already gets the hard
case right. But it still makes things up on ambiguous dictations (*"has
llegado a muchos proyectos"*, *"un buen número de clientes"* — none of that
was said).

**What was built** (detail in [HARNESS.md](HARNESS.md)):

1. **Sample and choose** — 3 candidates, scored with a deterministic
   function (coverage of the day + precision), keep the best one. Before,
   it took the first one that passed the filter.
2. **Sentence-by-sentence verification** — ask about each claim: *"is this
   stated in the text? YES/NO"*.
3. **Fallback cascade** — direct → extract+write → bare facts.
4. **Failure budget** — the last resort can't hallucinate because it
   doesn't generate prose.

### The mistake we made and how it was caught

The first version of the verifier **answered NO to everything**, including
sentences taken literally from the text. Result: it knocked down a
candidate scoring 89 and the system fell through to plan B, producing
something much worse.

```
  candidata 1: nota 89
  descartadas por no estar en el texto: "jugaste al ajedrez con tu padre"
                                        "tu hermana preparó la cena"
  la via directa fallo, descompongo la tarea
  >>> "Hoy fue un día complicado... te hizo sentir frustrado"   ← peor
```

**Lesson:** a filter stricter than the generator eats the good output.

**The fix, in two parts:**

- Two safeguards: if the score is ≥65 it isn't verified, and if the
  verifier knocks down more than two thirds of the sentences, the
  **verifier is ignored** and we trust the deterministic score.
- Use a different model to verify. And here's the nice finding:

> **`qwen2.5:7b` gets 6/6 right as a verifier** — the very model we ruled
> out for writing badly. What makes it a bad writer (it sticks to the
> original text, it doesn't stray) is exactly what makes it a good judge.

---

## Phase 9 — What you ask the device for isn't your day

**The failure:** a real dictation that said *"lo más importante es que me
imprimas que tengo que devolver las llaves"*. Out came:

> *"Lo importante es que **te imprimas** que tienes que devolver las llaves"*

Ungrammatical. The model copied the request literally without understanding
that it was addressed **to Pichón**, not part of the account of the day.

**What was done:** `extraer_recordatorios()` separates the instructions to
the device ("recuérdame", "que me imprimas", "apúntame") **before** the
model writes anything, and pastes them back at the end already formatted.
The reminder never goes through the LLM, so it can't come out mangled.

**Result:**

> *"Hoy has trabajado bien, has acabado la jornada intensa y has
> acumulado la sensación de quemado del curro. **No olvides: tienes que
> devolver las llaves.**"*

---

## Phase 10 — RAM back to zero

**The requirement:** the Pi is used for other projects too. Pichón can't
keep 5 GB resident all day.

**What was done:** Ollama's `keep_alive` set to 0, with an explicit
sequence:

```
petición → carga 3B → redacta → SUELTA 3B
                              → carga 7B → verifica → SUELTA 7B
         → finally: suelta ambos pase lo que pase
```

The `finally` matters: without it, an exception would leave 5 GB occupied.

**Measured:**

| | RAM before | RAM after | Models loaded at the end |
|---|---|---|---|
| Fast case | 7645 MB | 7566 MB | none |
| Slow case | 7577 MB | 7637 MB | none |

Cost: the fast case goes from 9 s to 42 s (loading from scratch every
time). It's the right trade-off for a shared Pi.

---

## Phase 11 — Whisper: attacking the transcription

**The bottleneck that was left.** This is how Chrome transcribed a
dictation with fast speech:

> *"hoy día vienen trabajo bien eh se ha acabado la jornada intensa...
> he tenido más tarde... lo voy a mala... el clu de atletismo"*

*"he tenido más tarde"* means nothing, and *"el clu de atletismo"* is a
proper name cut in half. The model works well on top of that — it copies
instead of inventing, which is the right thing — but it can't fix what
can't be understood.

**What was done:**

- The browser records the audio with `MediaRecorder` **in parallel** with
  Chrome's recognition. You still see the text as you speak; the audio goes
  separately.
- On save, both things are sent. The Pi converts with `ffmpeg` to 16 kHz
  mono WAV and runs it through `whisper.cpp` with `-l es`.
- If Whisper returns something reasonable (>20 characters), it's used. If it
  fails, we fall back to Chrome's text. `WHISPER_ACTIVO` checks that the
  binary and the model exist before even trying.
- The journal stores both transcriptions, so they can be compared.

**Measured on the Pi 5:** 11 s of audio in 7.4 s with the `small` model —
faster than real time. A 2-minute dictation takes ~1.5 min to transcribe,
and then comes the distillation.

**Cost:** total wait goes up to 3-4 minutes with long dictations. For a
nightly process where you dictate and go to sleep, that's acceptable.

---

## Phase 12 — Pending task, not a reproach

**The failure:** the dictation said *"y luego no olvidar de hablar con Juan
Carlos García"* — a pending task. The summary came out as:

> *"Luego **has olvidado** hablar con Ricardo Fuentes."*

It inverts the meaning, and on top of that it reads like a reproach on a
piece of paper you read right after waking up.

**A reasoning model was considered** (`qwen3:4b`, `deepseek-r1:7b`) as a
comprehension layer. Before downloading anything, the baseline was measured
with the models already installed:

| Model | Correct calls distinguishing task from fact |
|---|---|
| `qwen2.5:7b-instruct` | **8/8** |
| `llama3.2:3b` | 4/8 (says TASK to everything: the correct calls are luck) |

**The reasoner wasn't needed.** The 7B that was already there — the same
one that verifies — solves the task. The downloads were cancelled.

And there were two reasons to distrust the reasoner on this machine: the
code already warned that *thinking* models sometimes burn through their
tokens reasoning and return an empty answer (`SIN_RAZONAR = True`), and on
a Pi with no GPU, reasoning costs hundreds of tokens before the first
useful word.

**Result:** 3/3 correct. *"Mañana tienes que hablar con Ricardo
García"*.

---

## Phase 13 — Measure instead of opine

**The methodological problem:** up to this point, almost everything had
been measured on **a single dictation**. That fixes that one day and says
nothing about the rest.

**20 runs of the same text** gave 20/20 with no failures, 3.6 cm of paper,
and small variability (287-315 characters). Good numbers — but on the known
case.

**Eight new days**, none of them alike: teaching volleyball classes, a
shoulder operation, a house move, an argument, passing a civil service
exam, a trip to Lisbon, money trouble, a flat day.

**Result: 4 out of 8 with some kind of failure.** A long way from 20/20.

| Failure | Example |
|---|---|
| Loses details | the move without the boxes |
| First person in the present tense | *"te dijo que **necesito** operarme"* |
| Broken grammar | *"has estado justo mes"* |
| Changes a verb | *"quiero ver el mirador"* → *"quieres hablar con la gente del mirador"* |

None of them is catastrophic — it doesn't invent whole days or invert who
beats whom — but the system **is tuned to one particular day**.

**And the test battery found a bug before it even ran.** The
`RASTROS_EJEMPLO` list contained `"jugaste al voleibol"`, put there to
catch the case where the model copied the example from the prompt. It would
have rejected a **correct** output the day the author taught volleyball
classes. It was removed.

> Every specific fix is a hypothesis about the world. You have to check it
> against cases that didn't inspire it.

---

## Phase 14 — The paper

With `LARGO_TICKET = 380` the tickets came out at 3.6 cm. Lowering it to
**310**, and asking the model for 280 characters instead of 350:

| | 380 | 310 |
|---|---|---|
| Paper | 3.6 cm | **3.5 cm** |
| Characters | 287-315 | **288-301** |
| Lines | 10-11 | **10 fixed** |
| Average time | 64 s | **38 s** |
| Failures | 0/20 | **0/12** |

The interesting part isn't the paper — 1 mm — but that **the time drops
from 64 to 38 seconds**: asking it for less text makes the model get it
right first time more often, and the 7B gets loaded fewer times.

A standard 58 mm roll yields around 270 tickets: nine months.

---

## What's still open

**Generalizing to days we haven't seen.** This is the most important one.
On the known dictation it gets 20/20; on eight new days, 4/8. The failures
are ordered by how easy they are to fix:

1. **First person in the present tense** — *"necesito operarme"*.
   `corregir_persona()` covers the preterite, the future and part of the
   present; some verbs are missing. Deterministic and cheap.
2. **Loses details** — the move without the boxes. Probably the 310-character
   limit squeezing too hard; it would need measuring.
3. **Occasional broken grammar** — *"has estado justo mes"*. That's the 3B
   tripping up; you'd attack it with one more candidate or with a judge.

**Measure Whisper against Chrome on real dictations.** The journal stores
both transcriptions (`texto` and `texto_navegador`). The first comparisons
clearly favour Whisper — *"Ricardo Fuentes"* versus *"el clu de
atletismo"* — but days still need to accumulate.

**The proper-noun dictionary grows with use.** `Zaragoza` came out as
*"Sara Goza"* until it was added to the Whisper prompt and to
`WHISPER_ARREGLOS`. Streets, neighbourhoods and colleagues' names will keep
showing up.

---

## Where we started

| | At the start | Now |
|---|---|---|
| Real day | *"jugaste al tenis, al fútbol, al voleibol"* | work, the club, the keys |
| Chess | *"te ganaste dos partidas"* | *"tu padre te ganó dos partidas"* |
| Reminder | *"es que te imprimas que tienes que devolver las llaves"* | *"No olvides: tienes que devolver las llaves"* |
| Pending task | *"has olvidado hablar con Ricardo"* | *"tienes que hablar con Ricardo"* |
| Transcription | *"el clu de atletismo"* | *"el club de atletismo"* |
| Length | cut off mid-word | 10 lines, 3.5 cm |
| Idle RAM | 5 GB occupied | 0 |
