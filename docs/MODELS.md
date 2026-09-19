# Seven models, one test bench

All of them running on the same Raspberry Pi 5 (8 GB), via Ollama, with the
same prompt and the same temperature (0.3). The outputs are real.

**Except for the harness row:** that one generates three candidates at 0.25,
0.40 and 0.55, and keeps the best. It is not directly comparable with the
rest, which are a single generation at 0.3.

---

## The test bench

**Case A — agent inversion.** The other person wins, not the narrator. This
is the case no model solved on its own.

> *"por la tarde jugué al ajedrez con mi padre y me ganó dos partidas
> seguidas, luego mi hermana nos preparó la cena"*

(The dictation says: "in the afternoon I played chess with my father and he
beat me two games in a row, then my sister made us dinner". The point is that
the model has to respect who won.)

Correct: *"tu padre te ganó"* ("your father beat you"). Incorrect:
*"te ganaste"*, *"ganaste"*, *"le ganaste"* (all of which put the win on the
narrator's side).

**Case B — real, ambiguous dictation.** Broken speech, with filler words and
a request addressed to the device.

> *"bueno primer día de trabajo hoy sin el área intensivo aquí seguimos
> bien concentrado hemos llegado bastante de curro sí contento... y
> recuérdame porfa que lo más importante para hoy es devolver las llaves"*

Correct: it picks up work, the club and the keys without inventing activities.

---

## Results

Eight rows, seven models: `llama3.2:3b` appears twice, with the harness and
without it.

| Model | Size | Case A | Case B | Speed | Verdict |
|---|---|---|---|---|---|
| **`llama3.2:3b` + harness** | 2.0 GB | good, 4/4 | good | 9-22 s | **the chosen one** |
| `llama3.2:3b` alone | 2.0 GB | fails | so-so | 9-22 s | best baseline |
| `qwen2.5:7b-instruct` | 4.7 GB | fails, copies the original | so-so | 29-123 s | **good verifier** |
| `llama3.1:8b` | 4.9 GB | inverts the result | hallucinates | 24-40 s | discarded |
| `mistral:7b` | 4.4 GB | inverts the result | 1st person plural | 20-47 s | discarded |
| `glm4:9b` | 5.5 GB | fails | — | 322 s | discarded |
| `gemma2:2b` | 1.6 GB | fails | hallucinates | 6-15 s | discarded |
| `phi3:3.8b` | 2.2 GB | inverts the result | broken Spanish | 11-23 s | discarded |

---

## The outputs, one by one

### `llama3.2:3b` + harness — the chosen one

```
Caso A: "Hoy por la tarde jugaste al ajedrez con tu padre y tu padre te
         ganó dos partidas seguidas, luego tu hermana preparó la cena."
        4 de 4 intentos correctos

Caso B: "Hoy has trabajado bien, has acabado la jornada intensa y has
         acumulado la sensación de quemado del curro.
         No olvides: tienes que devolver las llaves."
```

Without the harness, the same model wrote *"te ganaste dos partidas"* (giving
the win to the narrator) and *"lo saqué"* in the first person.

---

### `qwen2.5:7b-instruct` — bad writer, excellent judge

```
Caso B: "Hoy me levantaste tarde fatal, luego jugaste al tenis con Clara
         y me ha ganado ella 6-3, estuve toda la tarde con el conector..."
```

It mixes first and second person in the same sentence and barely rewrites
anything: it copies the dictation, changing the odd verb here and there.
Tested at temperature 0.1, 0.3 and 0.5 — identical, so it is not randomness
but bias.

**But as a verifier it gets 6/6:**

| Statement | Expected | Answered |
|---|---|---|
| "jugaste al ajedrez con tu padre" | YES | YES |
| "tu hermana preparó la cena" | YES | YES |
| "tu padre te ganó dos partidas" | YES | YES |
| "fuiste a la playa con tus primos" | NO | NO |
| "comiste paella mirando el mar" | NO | NO |
| "estudiaste para el examen" | NO | NO |

Its rigidity is a flaw when writing and a virtue when judging. **It is the
system's verifier.**

---

### `llama3.1:8b` — inverts and hallucinates

```
Caso A: "Hoy jugaste al ajedrez con tu padre y ganaste dos partidas
         seguidas. Luego, tu hermana te preparó la cena. No olvides que
         mañana tienes que estudiar para el examen."
```

It inverts the result **and** invents an exam that was never mentioned. In
another run it added *"traer tus libros"* ("bring your books").

---

### `mistral:7b` — switches person and tense

```
Caso A: "Tú jugaste ajedrez con tu padre y le ganaste dos partidas
         seguidas. Mañana recuerda que tu hermana preparará la cena."

Caso B: "Te recordamos que el primer día de trabajo sin el área intensivo
         fue exitoso. Aquí, mantuvimos una buena concentración.
         Llegamos bastante de curso y estamos contentos."
```

It inverts the result, moves dinner into the future, and in case B it shifts
to the first person plural with an odd corporate tone.

---

### `glm4:9b` — slow and undiscriminating

322 seconds per response. As a verifier it scores 3/6: it says "NO" to
everything, just like the 3B. Discarded for both reasons.

---

### `gemma2:2b` — invents things and mixes forms of address

```
Caso A: "Tu padre jugaste al ajedrez con usted por la tarde y usted ganó
         dos partidas seguidas."

Caso B: "Llegaste a la oficina sin el área intensivo, y te sentaste bien."
```

*"te sentaste bien"* ("you sat down well") is not in the dictation. And it
mixes the informal *tú* with the formal *usted* in the same sentence.

---

### `phi3:3.8b` — the worst at Spanish

```
Caso A: "Ayer por la tarde jugaste al ajedrez con tu padre y ganaste dos
         partidas seguidas."

Caso B: "Hiciste bien en mantener la concentra extranjera, llegaste bien
         y te sentiste bien."
```

*"la concentra extranjera"* means nothing at all (roughly "the foreign
concentrate" — *concentra* is not even a noun in Spanish). In another output
it wrote *"llegó bastante de Curro"*, capitalising *curro* — slang for "work"
— as if it were a person's name. Little Spanish in its training.

---

## What these numbers teach

**Size did not predict quality.** The best writer of the seven is the second
smallest. The 9B was the slowest and among the worst.

**They all fail the same case.** Inverting the agent when switching verb
person is hard for models in this range, and it is not fixed with parameters.
It was fixed with 40 lines of regex.

**Every task wants its own model.** The one that writes and the one that
verifies do not have to be the same — and here, deliberately, they are not.

**Speed matters more than it seems.** The 3B is 3× faster than the 8Bs. In a
system that loads and unloads models per request, that decides whether the
user waits 40 seconds or two minutes.

---

## Reproducibility notes

- Raspberry Pi 5, 8 GB, no swap, Raspberry Pi OS.
- Ollama, default quantisation (Q4_K_M for all of them).
- Temperature 0.3 except where stated; `num_predict` 200.
- **Warning about the timing measurements:** the first call to a model
  includes loading it from disk (~30-120 s depending on size). The ranges in
  the table run from "already loaded" to "cold load".
- An orphaned `llama-server` process can hold on to GBs without `ollama ps`
  showing it. If a model fails with `KeyError: 'response'`, it is out of
  memory: `sudo systemctl restart ollama`.
