# Harness engineering

> **Harness**: everything surrounding the call to the model. How you
> prepare the input, how you validate the output, what you retry, what
> you correct deterministically, and what you do when nothing works.
>
> The model is one piece. The harness is the machinery around it.

This document explains the architecture that makes a 3B model — which on
its own gets it wrong — produce reliable results.

> The code is written in Spanish. Function and constant names appear
> here as they are in the source: `destilar()`, `LARGO_TICKET`, and so
> on. Model outputs are also kept in Spanish: they are the evidence.

---

## The principle

> **Don't ask the model for what a regex can do.
> Don't trust it to be right without checking.**

Whenever a deterministic rule is available, that rule beats praying to
the prompt. It sounds obvious; in practice it's hard, because the
temptation is always to rewrite the prompt one more time.

The number that backs it: **seven different models failed the same test.
The harness passed it.**

---

## The full pipeline

```
                     dictated text (broken, full of filler)
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  PREPARE THE INPUT          (deterministic, no LLM)           │
   │                                                                │
   │  limpiar()               strips "eh", "o sea", "sabes"         │
   │  extraer_recordatorios() splits "remind me that..." from the   │
   │                          account of the day                    │
   │  explicitar_sujeto()     "me ganó" → "mi padre me ganó"        │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  GENERATE                   llama3.2:3b                        │
   │                                                                │
   │  3 candidates, temperature 0.25 → 0.40 → 0.55                  │
   │  stops early if one scores ≥ 70                                │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  FILTER                     (deterministic, no LLM)            │
   │                                                                │
   │  validar_directo()   repeated sentences? invented words?       │
   │                      vocabulary not from the source text?      │
   │  puntuar()           0-100: coverage of the day + precision    │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
                        ┌──────────┴──────────┐
              score ≥65 and no      score <65, or sentences
              suspicious phrases    like "has olvidado..."
                        │                     │
                        │        ┌────────────▼────────────┐
                        │        │  JUDGE                  │
                        │        │  qwen2.5:7b-instruct    │
                        │        │  (the 3B is released)   │
                        │        │                          │
                        │        │  did it make this up?    │
                        │        │  "is this stated in      │
                        │        │   the text? YES/NO"      │
                        │        │                          │
                        │        │  task or fact?           │
                        │        │  "already done, or       │
                        │        │   still to do?"          │
                        │        │                          │
                        │        │  if it rejects 2/3 or    │
                        │        │  more, ignore the judge  │
                        │        └────────────┬────────────┘
                        └──────────┬──────────┘
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  CORRECT                    (deterministic, no LLM)            │
   │                                                                │
   │  corregir_persona()      "lo saqué" → "lo sacaste"             │
   │  arreglar_concordancia() "él te ganaste" → "él te ganó"        │
   │  arreglar_intencion()    "has olvidado X" → "tienes que X"     │
   │  recortar_a_frase()      ≤310 chars, never mid-word            │
   │  con_recordatorio()      appends "No olvides: ..." at the end  │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
                        paragraph for the ticket
```

---

## The seven pieces

### 1. Prepare the input

What you fix before the model reads it, the model cannot break.

**`limpiar()`** — strips filler words with regex. Saves the model half
the work and removes noise it could otherwise pull on to invent things.

**`extraer_recordatorios()`** — separates what you're asking the device
to do from what you're telling it about your day. Born from a real
failure:

```
dictated: "the important thing is to print that I have to return the keys"
without:  "The important thing is that you print that you have to
           return the keys"                                          bad
with:     "Don't forget: you have to return the keys."               ok
```

The reminder **never passes through the LLM**, so it cannot be mangled.

**`explicitar_sujeto()`** — the piece that solved what no model solved.
The finding: with the subject explicit and up front, the model is always
right; with the subject implicit, it always fails.

```
input:     "jugué al ajedrez con mi padre y me ganó dos partidas"
rewritten: "jugué al ajedrez con mi padre y mi padre me ganó dos partidas"
```

(*"he beat me two games"* becomes *"my father beat me two games"*.)

---

### 2. Generate several, pick the best

A 3B is **unstable**: the same input produces one good output and one
disastrous one. Taking the first that passes the filter wastes that.

```python
for intento in range(3):
    m = llamar(p, temp=0.25 + 0.15*intento)
    ...
    nota = puntuar(m, limpio)
    candidatas.append((nota, m))
    if nota >= 70:        # it won't get better: stop spending
        break
candidatas.sort(reverse=True)
```

**`puntuar()` spends no LLM time.** It measures two things about the
vocabulary:

- **coverage** — how much of the day the paragraph captures
- **precision** — how much of the paragraph comes from the day

```
  60  good                 "Hoy fue tu primer día de trabajo..."
  18  partly invented      "...llegaste a muchos clientes y proyectos"
   0  fully invented       "Hoy jugaste al tenis, al fútbol..."
```

---

### 3. Filter the impossible

**`validar_directo()`** rejects three things, all detectable without an
LLM:

| What it catches | Real example |
|---|---|
| Repeated sentences | *"Hoy jugaste al tenis, hoy jugaste al fútbol, hoy jugaste al voleibol"* |
| Invented words | *"la saquéste"*, *"juegaste"* — neither is a Spanish word |
| Foreign vocabulary | more than 1/3 of words not present in the source text |

**A subtle bug that had to be fixed:** the first version compared word
stems by 5 characters, which **penalised correct conjugations** —
"jugaste" doesn't share 5 characters with "jugué". The validator was
punishing exactly what the model got right. It now compares stems after
stripping verb endings.

---

### 4. Judge with a different model

The most expensive piece, and the one that needs the most care.

**The finding:** `qwen2.5:7b` scores **6/6** on verification. It's the
same model discarded for writing badly. Not a contradiction: what makes
it a bad writer — it sticks to the source text, it won't move away from
it — is exactly what makes it a good judge.

| Model | Verification accuracy |
|---|---|
| `qwen2.5:7b-instruct` | **6/6** |
| `llama3.2:3b` | 3/6 (says NO to everything) |
| `glm4:9b` | 3/6 (says NO to everything) |

**The two safeguards**, both born from a real failure (schematic; the
real code is in `destilar_directo()`):

```python
if nota >= 65:
    return mejor            # no need to verify, saves ~20 s per sentence

buenas, malas = verificar_parrafo(mejor, limpio)

if len(buenas) <= len(malas) / 2:
    return mejor            # THE VERIFIER is what's failing, not the text
```

Without the second one, a candidate scoring 89 was destroyed by a
verifier that answered NO to everything.

**The same model does a second job: telling a task from a fact.** The
dictation said *"no olvidar de hablar con Ricardo"* — "don't forget to
talk to Ricardo", something still pending — and the 3B wrote *"has
olvidado hablar con Ricardo"*: "you forgot to talk to Ricardo". It flips
the meaning and reads like a reproach.

| Model | Task/fact accuracy |
|---|---|
| `qwen2.5:7b-instruct` | **8/8** |
| `llama3.2:3b` | 4/8 (says TASK to everything) |

`arreglar_intencion()` only asks when the paragraph contains suspicious
phrases (*"has olvidado"*, *"te olvidaste de"*). If there are none, the
7B isn't even loaded. And when both jobs are needed — verification and
intent — it loads once.

**Why not a reasoning model.** Adding a reasoner (`qwen3:4b`,
`deepseek-r1:7b`) as a comprehension layer was considered. It wasn't
needed: the 7B already installed scores 8/8. And there were two reasons
for suspicion — the code already warned that *thinking* models sometimes
burn their token budget reasoning and return an empty answer
(`SIN_RAZONAR = True`), and on a GPU-less Pi reasoning costs hundreds of
tokens before the first useful word.

---

### 5. Correct deterministically

What the model can't do, rules do.

**`corregir_persona()`** — the model copies verb forms straight from the
dictation instead of conjugating them into the second person. It covers
irregulars (`fui→fuiste`, `estuve→estuviste`), present tense
(`estoy→estás`), regulars with their spelling shifts (`saqué→sacaste`,
`llegué→llegaste`, `empecé→empezaste`) and pronouns (`me→te`, `mi→tu`).

**The hard part was not breaking correct text.** The first version
turned "café" into "cafaste" and "allí" into "alliste" — nonsense,
because those aren't verbs, they just end the same way. There's now an
exception list and a minimum stem length.

**`arreglar_concordancia()`** — *"él te ganaste"* is ungrammatical in
Spanish: third-person subject with a second-person verb. When it
appears, we know **with certainty** that the verb is wrong, and it gets
moved to third person. There's no ambiguity to resolve.

---

### 6. Fallback cascade

```
destilar_directo()            3 candidates + verification
        │ fails
        ▼
destilar_por_hechos()         extract facts → write them up
        │ fails                (two easy tasks instead of one hard one)
        ▼
destilar_a_prueba_de_fallos() the facts as-is, unwritten
                              (can't hallucinate: it generates no prose)
```

In a personal diary, plain unadorned facts beat an invented day.

---

### 7. RAM goes back to zero

The Pi is shared with other projects, so no model stays resident.

```
HTTPS request
   │
   ├─▶ load 3B ──▶ write ──▶ RELEASE 3B
   │                              │
   │                              ├─▶ [if needed] load 7B
   │                              │      verify N sentences
   │                              │      RELEASE 7B on the last one
   │                              │
   └─▶ finally: release both ──▶ RAM at zero
```

**The two models never coexist.** The peak is always a single model. The
`finally` guarantees release even if an exception is thrown.

Measured: 7645 MB free before, 7566 MB after, `ollama ps` empty.

---

## What it cost to learn

**A filter stricter than the generator eats the good output.** The
verifier knocked down a candidate scoring 89. The lesson isn't "don't
verify": it's "measure whether your verifier actually discriminates
before trusting it" — and have a way out when it doesn't.

**An example inside a prompt rule gets copied.** The rule said *"speak
in the second person: today you played tennis..."* and the model wrote
three sentences about sports it had invented. Examples teach format;
they must never carry plausible content.

**Small models are unstable, not bad.** The same input yields an
excellent output and a disastrous one. Generating three and picking the
best exploits that better than hammering the prompt.

**Each model has its job.** The best writer isn't the best verifier. And
the worst writer may be the best judge.

**The harness beats the parameters.** Seven models from 2B to 9B, and
the one that solved the problem was the 3B — with machinery around it,
and three times faster than the 8B models.

---

## What the harness does NOT fix

This section exists because almost everything measured above was
measured against **a single dictation**. On that text the system scores
20 out of 20. On days it had never seen, **it fails half the time**.

Eight different days were tested (teaching volleyball, a shoulder
operation, a house move, an argument, passing an exam, a trip, money
worries, a day where nothing happens). Four came out clean. The other
four:

| Failure | Real example |
|---|---|
| Drops details | *"we emptied the whole flat, with boxes"* → the summary never mentions the boxes |
| First person in present tense | *"te dijo que **necesito** operarme"* — "it told you that **I** need surgery" |
| Broken grammar | *"has estado justo mes"*, *"Tú has estado"* |
| Changes a verb | *"I want to **see** the viewpoint"* → *"you want to **talk to the people** at the viewpoint"* |

None is catastrophic — it doesn't invent whole days or flip who won a
game, which were the serious failures — but they're there.

**The methodological lesson**, which is worth more than the specifics:

> Optimising against a single test case produces a system that solves
> that case. Every specific fix is a hypothesis about the world, and it
> has to be checked against cases that didn't inspire it.

An example of what that uncovered: the `RASTROS_EJEMPLO` list once
contained `"jugaste al voleibol"` — "you played volleyball" — added to
catch a case where the model was copying the prompt's own example. It
would have rejected a **correct** output the day the author started
teaching volleyball. It was removed while building the varied-days
battery, before even running it.

Closed lists (family relations, names, irregular verbs) all carry that
risk. The robust approach is the one in `validar_directo()`: compare
vocabulary against the source text, which doesn't depend on enumerating
the world.
