# Harness engineering

> **Harness**: todo lo que rodea a la llamada al modelo. Cómo se prepara
> la entrada, cómo se valida la salida, qué se reintenta, qué se corrige
> de forma determinista, y qué se hace cuando nada funciona.
>
> El modelo es una pieza. El harness es la máquina alrededor.

Este documento explica la arquitectura que hace que un modelo de 3B
—que solo no acierta— produzca resultados fiables.

---

## El principio

> **No le pidas al modelo lo que puede hacer un regex.
> No te fíes de que acierte sin verificarlo.**

Cada vez que existe una regla determinista disponible, esa regla gana a
rezarle al prompt. Suena obvio; en la práctica cuesta, porque la
tentación siempre es reescribir el prompt una vez más.

El dato que lo justifica: **siete modelos distintos fallaron la misma
prueba. El harness la pasó.**

---

## El recorrido completo

```
                      texto dictado (roto, con muletillas)
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  PREPARAR LA ENTRADA        (determinista, sin LLM)           │
   │                                                                │
   │  limpiar()               quita "eh", "o sea", "sabes"          │
   │  extraer_recordatorios() separa "recuérdame que..." del relato │
   │  explicitar_sujeto()     "me ganó" → "mi padre me ganó"        │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  GENERAR                    llama3.2:3b                        │
   │                                                                │
   │  3 candidatas, temperatura 0.25 → 0.40 → 0.55                  │
   │  corta antes si alguna saca nota ≥ 70                          │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  FILTRAR                    (determinista, sin LLM)            │
   │                                                                │
   │  validar_directo()   ¿frases repetidas? ¿palabras inventadas?  │
   │                      ¿vocabulario que no viene del texto?      │
   │  puntuar()           0-100: cobertura del día + precisión      │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
                        ┌──────────┴──────────┐
              nota ≥65 y sin        nota <65, o hay frases
              frases sospechosas    tipo "has olvidado..."
                        │                     │
                        │        ┌────────────▼────────────┐
                        │        │  JUZGAR                 │
                        │        │  qwen2.5:7b-instruct    │
                        │        │  (el 3B ya se soltó)    │
                        │        │                          │
                        │        │  ¿se lo ha inventado?    │
                        │        │  "¿está esto dicho       │
                        │        │   en el texto? SÍ/NO"    │
                        │        │                          │
                        │        │  ¿tarea o hecho?         │
                        │        │  "¿ya lo hizo, o tiene   │
                        │        │   que hacerlo?"          │
                        │        │                          │
                        │        │  ⚠️ si tumba 2/3 o más,  │
                        │        │     se ignora el juez    │
                        │        └────────────┬────────────┘
                        └──────────┬──────────┘
                                   │
   ┌───────────────────────────────▼───────────────────────────────┐
   │  CORREGIR                   (determinista, sin LLM)            │
   │                                                                │
   │  corregir_persona()      "lo saqué" → "lo sacaste"             │
   │  arreglar_concordancia() "él te ganaste" → "él te ganó"
   │  arreglar_intencion()    "has olvidado X" → "tienes que X"      │
   │  recortar_a_frase()      ≤310 car., nunca a media palabra      │
   │  con_recordatorio()      pega "No olvides: ..." al final       │
   └───────────────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
                          párrafo para el ticket
```

---

## Las siete piezas

### 1. Preparar la entrada

Lo que se arregla antes de que el modelo lea, no se le puede estropear
después.

**`limpiar()`** — quita muletillas con regex. Ahorra al modelo medio
trabajo y reduce el ruido del que puede tirar para inventar.

**`extraer_recordatorios()`** — separa lo que le pides al aparato de lo
que cuentas de tu día. Nació de un fallo real:

```
dictado:  "lo más importante es que me imprimas que tengo que echar CVs"
sin esto: "Lo importante es que te imprimas que tienes que echar CVS"  ✗
con esto: "No olvides: tienes que echar CVS."                          ✓
```

El recordatorio **nunca pasa por el LLM**, así que no puede deformarse.

**`explicitar_sujeto()`** — la pieza que resolvió lo que ningún modelo
resolvió. El descubrimiento: con el sujeto explícito y delante, el
modelo acierta siempre; con el sujeto implícito, falla siempre.

```
entrada:  "jugué al ajedrez con mi padre y me ganó dos partidas"
reescrito:"jugué al ajedrez con mi padre y mi padre me ganó dos partidas"
```

---

### 2. Generar varias y elegir

Un 3B es **inestable**: la misma entrada produce una salida buena y otra
desastrosa. Quedarse con la primera que pasa el filtro desaprovecha eso.

```python
for intento in range(3):
    m = llamar(p, temp=0.25 + 0.15*intento)
    ...
    nota = puntuar(m, limpio)
    candidatas.append((nota, m))
    if nota >= 70:        # ya no va a mejorar: no gastamos más
        break
candidatas.sort(reverse=True)
```

**`puntuar()` no gasta LLM.** Mide dos cosas sobre el vocabulario:

- **cobertura** — cuánto del día recoge el párrafo
- **precisión** — cuánto del párrafo viene del día

```
  60  buena                "Hoy fue tu primer día de trabajo..."
  18  con invento          "...llegaste a muchos clientes y proyectos"
   0  inventada del todo   "Hoy jugaste al tenis, al fútbol..."
```

---

### 3. Filtrar lo imposible

**`validar_directo()`** rechaza tres cosas, todas detectables sin LLM:

| Qué detecta | Ejemplo real |
|---|---|
| Frases repetidas | *"Hoy jugaste al tenis, hoy jugaste al fútbol, hoy jugaste al voleibol"* |
| Palabras inventadas | *"la saquéste"*, *"juegaste"* |
| Vocabulario ajeno | más de 1/3 de palabras que no vienen del texto |

**Un bug sutil que tuvo que arreglarse:** la primera versión comparaba
raíces por 5 caracteres, y eso **penalizaba las conjugaciones correctas**
— "jugaste" no comparte 5 caracteres con "jugué". El validador castigaba
justo lo que hacía bien. Ahora compara raíces quitando terminaciones
verbales.

---

### 4. Verificar con otro modelo

La pieza más cara y la que más cuidado necesita.

**El hallazgo:** `qwen2.5:7b` acierta **6/6** verificando. Es el mismo
modelo que descartamos por redactar mal. No es contradicción: lo que le
hace mal redactor —se pega al texto original, no se aleja— es justo lo
que le hace buen juez.

| Modelo | Aciertos verificando |
|---|---|
| `qwen2.5:7b-instruct` | **6/6** |
| `llama3.2:3b` | 3/6 (dice NO a todo) |
| `glm4:9b` | 3/6 (dice NO a todo) |

**Las dos salvaguardas**, ambas nacidas de un fallo real
(esquemático; el código real está en `destilar_directo()`):

```python
if nota >= 65:
    return mejor            # no hace falta verificar, y ahorra ~20s/frase

buenas, malas = verificar_parrafo(mejor, limpio)

if len(buenas) <= len(malas) / 2:
    return mejor            # el que falla es EL VERIFICADOR, no el texto
```

Sin la segunda, una candidata con nota 89 fue destruida por un
verificador que respondía NO a todo.

**El mismo modelo hace un segundo trabajo: distinguir tarea de hecho.**
El dictado decía *"no olvidar de hablar con Juan Carlos"* (pendiente) y
el 3B escribía *"has olvidado hablar con Juan Carlos"* — invierte el
sentido y encima suena a reproche.

| Modelo | Aciertos tarea/hecho |
|---|---|
| `qwen2.5:7b-instruct` | **8/8** |
| `llama3.2:3b` | 4/8 (dice TAREA a todo) |

`arreglar_intencion()` solo pregunta cuando el párrafo tiene frases
sospechosas (*"has olvidado"*, *"te olvidaste de"*). Si no las hay, el
7B ni se carga. Y cuando ambas cosas tocan —verificar e intención— se
carga una sola vez.

**Por qué no un modelo de razonamiento.** Se planteó añadir un
razonador (`qwen3:4b`, `deepseek-r1:7b`) como capa de comprensión. No
hizo falta: el 7B ya instalado acierta 8/8. Y había dos razones para
desconfiar — el código ya avisaba de que los modelos *thinking* a veces
agotan los tokens razonando y devuelven la respuesta vacía
(`SIN_RAZONAR = True`), y en una Pi sin GPU razonar cuesta cientos de
tokens antes de la primera palabra útil.

---

### 5. Corregir de forma determinista

Lo que el modelo no sabe hacer, lo hacen reglas.

**`corregir_persona()`** — el modelo copia las formas verbales del
dictado en vez de conjugarlas. Cubre irregulares (`fui→fuiste`,
`estuve→estuviste`), presentes (`estoy→estás`), regulares con su cambio
ortográfico (`saqué→sacaste`, `llegué→llegaste`, `empecé→empezaste`) y
pronombres (`me→te`, `mi→tu`).

**Lo que más trabajo costó fue no romper texto correcto.** La primera
versión convertía "café" en "cafaste" y "allí" en "alliste". Hay una
lista de excepciones y un mínimo de longitud de raíz.

**`arreglar_concordancia()`** — *"él te ganaste"* es agramatical en
español. Cuando aparece, sabemos **con certeza** que el verbo está mal
conjugado, y se pasa a tercera persona. No hay ambigüedad que resolver.

---

### 6. Cascada de respaldo

```
destilar_directo()            3 candidatas + verificación
        │ falla
        ▼
destilar_por_hechos()         extraer hechos → redactarlos
        │ falla                (dos tareas fáciles en vez de una difícil)
        ▼
destilar_a_prueba_de_fallos() los hechos tal cual, sin redactar
                              (no puede alucinar: no genera prosa)
```

En un diario personal, unos hechos sin adornar son preferibles a un día
inventado.

---

### 7. La RAM vuelve a cero

La Pi se comparte con otros proyectos, así que ningún modelo queda
residente.

```
petición HTTPS
   │
   ├─▶ carga 3B ──▶ redacta ──▶ SUELTA 3B
   │                                │
   │                                ├─▶ [si hace falta] carga 7B
   │                                │      verifica N frases
   │                                │      SUELTA 7B en la última
   │                                │
   └─▶ finally: suelta ambos ──▶ RAM a cero
```

**Nunca coexisten los dos modelos.** El pico es siempre uno solo. El
`finally` garantiza la liberación incluso si salta una excepción.

Medido: 7645 MB libres antes, 7566 MB después, `ollama ps` vacío.

---

## Lo que costó aprender

**Un filtro más estricto que el generador se come lo bueno.** El
verificador tumbó una candidata con nota 89. La lección no es "no
verifiques", sino "mide si tu verificador discrimina antes de confiar en
él" — y ten una salida cuando no lo haga.

**Un ejemplo dentro de una regla del prompt se copia.** La regla decía
*"habla en segunda persona: hoy jugaste al tenis..."* y el modelo escribió
tres frases sobre deportes. Los ejemplos enseñan formato; nunca deben
llevar contenido plausible.

**Los modelos pequeños son inestables, no malos.** La misma entrada da
una salida excelente y otra desastrosa. Generar tres y elegir aprovecha
eso mejor que insistir en el prompt.

**Cada modelo tiene su tarea.** El que mejor redacta no es el que mejor
verifica. Y el que peor redacta puede ser el mejor juez.

**El harness gana a los parámetros.** Siete modelos de 2B a 9B, y el que
resolvió el problema fue el de 3B — con maquinaria alrededor, y siendo
tres veces más rápido que los de 8B.

---

## Lo que el harness NO arregla

Esta sección existe porque casi todo lo medido hasta aquí se midió sobre
**un solo dictado** — el de trabajo, currículums y Juan Carlos. Sobre
ese texto el sistema acierta 20 de 20. Sobre días que nunca había visto,
**falla la mitad de las veces**.

Se probaron ocho días distintos (dar clases de voleibol, una operación
de hombro, una mudanza, una discusión, aprobar una oposición, un viaje a
Lisboa, preocupación por dinero, un día plano). Cuatro salieron limpios.
Los otros cuatro:

| Fallo | Ejemplo real |
|---|---|
| Pierde detalles | *"hemos vaciado el piso con cajas"* → el resumen no menciona las cajas |
| Primera persona en presente | *"te dijo que **necesito** operarme"* |
| Gramática rota | *"has estado justo mes"*, *"Tú has estado"* |
| Cambia un verbo | *"quiero **ver** el mirador"* → *"quieres **hablar con la gente** del mirador"* |

Ninguno es catastrófico —no inventa días enteros ni invierte quién gana
a quién, que eran los fallos graves— pero están ahí.

**La lección metodológica**, que vale más que el detalle concreto:

> Optimizar contra un solo caso de prueba produce un sistema que resuelve
> ese caso. Cada arreglo específico es una hipótesis sobre el mundo, y
> hay que comprobarla contra casos que no la inspiraron.

Un ejemplo de lo que eso destapó: la lista `RASTROS_EJEMPLO` llegó a
contener `"jugaste al voleibol"`, añadido para cazar un caso en que el
modelo copiaba el ejemplo del prompt. Habría rechazado una salida
**correcta** el día que el autor empezara a dar clases de voleibol. Se
quitó al construir la batería de días variados — antes de ejecutarla
siquiera.

Las listas cerradas (parentescos, nombres, verbos irregulares) tienen
todas ese riesgo. La vía robusta es la de `validar_directo()`: comparar
vocabulario contra el texto original, que no depende de enumerar el
mundo.
