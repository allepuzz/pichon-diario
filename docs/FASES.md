# El recorrido, fase a fase

Cada fase documenta tres cosas: **en qué estado estaba el sistema**, **qué
falló**, y **qué se cambió**. Las salidas que aparecen son reales,
copiadas de las pruebas.

El caso de prueba que atraviesa todo el documento es este dictado, con
dos trampas puestas a propósito — muletillas y una inversión de agente
(gana la otra persona, no quien cuenta):

> *"pues hoy eh me levante tarde o sea fatal y luego jugue al tenis con
> Marta y me ha ganado ella 6-3 sabes, estuve toda la tarde con el
> conector de la impresora que estaba mal puesto al final lo saque estoy
> contento pero agotado"*

---

## Fase 1 — Infraestructura

**Estado:** el LLM corría en una tablet Android con Termux, 4 GB de RAM.
La Raspberry Pi estaba sin configurar.

**Problema:** 4 GB no dan para un modelo decente, y la tablet tenía que
estar encendida y despierta toda la noche.

**Qué se hizo:** mover todo a la Pi 5 (8 GB). SSH por clave, Ollama
instalado, servidor Flask desplegado.

**Resultado:** el ciclo funciona, pero la Pi vivía del hotspot del móvil
y el servidor se lanzaba con `nohup` — moría en cada reinicio.

---

## Fase 2 — Las dos redes y el arranque automático

**Estado:** Pichón funcionaba mientras nadie tocara nada.

**Problemas encontrados:**

1. La Pi solo conocía el hotspot del móvil. Por la mañana, con el
   hotspot apagado, no había ticket.
2. `nohup` no sobrevive a un reinicio.
3. El ESP32 apuntaba a una IP fija, y la IP cambia según la red.

**Qué se hizo:**

- Dos perfiles de red con prioridad (casa 10, hotspot 5). La Pi se
  engancha a la que encuentre.
- **mDNS**: la Pi se llama `pichon` y responde a `pichon.local`. El
  ESP32 pregunta por nombre, así que la IP da igual.
- `systemd` con `After=ollama.service` y `Restart=always`.

**Una trampa que costó una sesión:** al conectar la Pi a la red de casa
desde SSH, se corta la propia sesión. La solución fue un servicio
transitorio con `systemd-run` que intenta la conexión, espera, y vuelve
al hotspot pase lo que pase.

**Resultado:** reinicio en frío verificado. La Pi arranca sola, se
conecta sola, y el servidor levanta solo. ✅

---

## Fase 3 — Las citas del libro

**Estado:** el archivo de frases tenía 181 fragmentos troceados del
libro, sin verificar.

**Problema:** los fragmentos habían perdido el sentido. *"That stops the
positive."* o *"Life is real."* no son citas, son trozos de frases más
largas. El libro tiene voz; el troceado la mataba.

**Qué se hizo:** leer el PDF entero (196 páginas, 100.561 caracteres),
extraer 109 citas completas que se sostengan solas, y **verificar cada
una literalmente contra el original**. El verificador encontró 10 no
literales en la primera pasada; se corrigieron una a una.

**Resultado:** 109 citas, 0 no literales. La rotación las recorre sin
repetir durante 109 días.

```
  I knew a guy once who was down because he didn't have any friends.
  I took him to the book store and told him to look around --
  he had thousand of good friends on the shelves.
```

*(`thousand` es errata del libro original; se respeta, son citas exactas.)*

---

## Fase 4 — El ESP32 imprime

**Estado:** servidor funcionando, ESP32 con un sketch que apuntaba a una
IP fija y esperaba hardware que no existía.

**Qué se cambió en el sketch:**

| | Antes | Ahora |
|---|---|---|
| Redes | una, sin rellenar | dos, con `WiFiMulti` |
| Destino | IP fija | `pichon.local` + IPs de respaldo |
| Protocolo | HTTP | HTTPS con `setInsecure()` |
| Encendido | MOSFET en GPIO25 | nada: fuente propia |
| Prueba | botón en GPIO33 | RESET del ESP32 |

**Un detalle que habría roto el montaje:** el sketch habilitaba
`ext0_wakeup` sobre GPIO33. Sin botón cableado, ese pin flota y puede
despertar el ESP32 solo, imprimiendo tickets a deshoras.

**Resultado:** ✅ imprime.

---

## Fase 5 — El primer fallo de verdad: alucina deportes

**Estado:** todo el circuito funciona. Llega el primer dictado real, que
hablaba de trabajo, el papeleo y devolver las llaves.

**Lo que imprimió:**

> *"Hoy jugaste al tenis, hoy jugaste al fútbol, hoy jugaste al voleibol."*

No había ni un deporte en el dictado. Y el recordatorio que el usuario
pidió expresamente —devolver las llaves— desapareció.

**La causa, y es instructiva:** el prompt contenía esta regla:

```
- Habla en segunda persona: "hoy jugaste al tenis...".
```

El modelo de 3B **copió el ejemplo** en vez de aplicarlo, y luego siguió
el patrón inventando dos deportes más. El propio archivo ya avisaba de
esto —"el ejemplo debe enseñar el FORMATO, nunca el contenido"— pero la
regla se había escrito con un ejemplo concreto.

**Qué se hizo:**

1. Reescribir la regla sin ejemplos con contenido real.
2. Añadir `validar_directo()`: rechaza frases repetidas y salidas cuyo
   vocabulario no venga del texto original.
3. Una regla nueva: *"Si te pidió recordar algo para mañana, eso va
   SIEMPRE en el párrafo"*.

**Resultado:**

> *"Hoy has estado bien concentrado en el trabajo... No olvides recordar
> la el papeleo y la suerte para el domingo en la iglesia de Santo
> Domingo, y recuérdame que lo más importante para hoy es mandar
> las llaves."*

---

## Fase 6 — Quién gana a quién

**Estado:** ya no inventa actividades. Pero aparece un fallo más sutil.

**El problema:** con el dictado *"jugué al ajedrez con mi padre y me ganó
dos partidas"*, el modelo escribía:

> *"jugaste al ajedrez con tu padre y **te ganaste** dos partidas"*

Invierte el resultado, o lo deja sin sentido. **4 de 4 intentos fallidos.**

**Lo que NO funcionó** — seis variantes de prompt, todas probadas.
Las tres más representativas:

| Variante | Resultado |
|---|---|
| Regla acotada ("lo que hicieron OTRAS personas va en tercera") | *"le ganaste"* — invierte igual |
| Regla explícita sobre ganar/perder | *"te ganaste"* — sin cambios |
| "Nombra siempre al sujeto" | *"tu padre jugaste al ajedrez contigo"* — agramatical |

**El descubrimiento:** cuando el sujeto va **explícito y delante**, el
modelo acierta siempre. *"ella te ganó 6-3"*, correcto en todas las
pruebas. Falla solo cuando está implícito.

**Qué se hizo** — atacar por los dos lados, sin tocar el modelo:

- `explicitar_sujeto()` reescribe la **entrada** antes de que el modelo
  la lea: *"me ganó dos partidas"* → *"mi padre me ganó dos partidas"*.
- `arreglar_concordancia()` corrige la **salida**: *"él te ganaste"* es
  agramatical en español, así que cuando aparece sabemos con certeza que
  el verbo está mal conjugado, y lo pasamos a tercera persona.

**Resultado:** 4 de 4 correctas.

> *"Hoy por la tarde jugaste al ajedrez con tu padre y **tu padre te ganó**
> dos partidas seguidas, luego tu hermana preparó la cena."*

---

## Fase 7 — ¿Y si probamos un modelo más grande?

**La hipótesis razonable:** un 3B se queda corto; con 7B, 8B o 9B esto se
arregla solo.

**No se arregló.** Los siete modelos sobre el mismo caso del ajedrez
(ocho filas: el 3B aparece con harness y sin él):

| Modelo | ¿Acierta quién gana? | ¿Inventa? |
|---|---|---|
| `llama3.2:3b` **+ harness** | ✅ 4/4 | no |
| `llama3.2:3b` solo | ❌ | no |
| `qwen2.5:7b-instruct` | ❌ copia el original | no reescribe |
| `llama3.1:8b` | ❌ invierte | sí: *"traer tus libros"*, *"estudiar para el examen"* |
| `mistral:7b` | ❌ invierte | sí: confunde tiempos verbales |
| `glm4:9b` | ❌ | — (322 s por respuesta) |
| `gemma2:2b` | ❌ | sí: *"te sentaste bien"* |
| `phi3:3.8b` | ❌ invierte | español roto: *"la concentra extranjera"* |

Detalle completo en [MODELOS.md](MODELOS.md).

**La conclusión que cambió el rumbo del proyecto:**

> Más parámetros no arreglan el problema. El harness sí. Y el 3B, además
> de acertar con ayuda, es **3 veces más rápido** que los 8B.

---

## Fase 8 — Harness engineering

**Estado:** el 3B con dos correctores deterministas ya acierta lo difícil.
Pero sigue inventando sobre dictados ambiguos (*"has llegado a muchos
proyectos"*, *"un buen número de clientes"* — nada de eso se dijo).

**Qué se montó** (detalle en [HARNESS.md](HARNESS.md)):

1. **Muestrear y elegir** — 3 candidatas, puntuadas con una función
   determinista (cobertura del día + precisión), quedarse con la mejor.
   Antes se cogía la primera que pasaba el filtro.
2. **Verificación frase a frase** — preguntar por cada afirmación:
   *"¿está esto dicho en el texto? SÍ/NO"*.
3. **Cascada de respaldo** — directa → extraer+redactar → hechos pelados.
4. **Presupuesto de fallo** — el último recurso no puede alucinar porque
   no genera prosa.

### El error que cometimos y cómo se detectó

La primera versión del verificador **respondía NO a todo**, incluidas
frases literales del texto. Resultado: tumbó una candidata con nota 89 y
el sistema cayó al plan B, produciendo algo mucho peor.

```
  candidata 1: nota 89
  descartadas por no estar en el texto: "jugaste al ajedrez con tu padre"
                                        "tu hermana preparó la cena"
  la via directa fallo, descompongo la tarea
  >>> "Hoy fue un día complicado... te hizo sentir frustrado"   ← peor
```

**Lección:** un filtro más estricto que el generador se come lo bueno.

**El arreglo, en dos partes:**

- Dos salvaguardas: si la nota es ≥65 no se verifica, y si el verificador
  tumba más de dos tercios de las frases, se **ignora al verificador** y
  se confía en la nota determinista.
- Usar otro modelo para verificar. Y aquí está el hallazgo bonito:

> **`qwen2.5:7b` acierta 6/6 verificando** — el mismo modelo que
> descartamos por redactar mal. Lo que le hace mal redactor (se pega al
> texto original, no se aleja) es exactamente lo que le hace buen juez.

---

## Fase 9 — Lo que le pides al aparato no es tu día

**El fallo:** dictado real que decía *"lo más importante es que me
imprimas que tengo que devolver las llaves"*. Salió:

> *"Lo importante es que **te imprimas** que tienes que devolver las llaves"*

Agramatical. El modelo copió la petición literal sin entender que iba
dirigida **a Pichón**, no al relato del día.

**Qué se hizo:** `extraer_recordatorios()` separa las instrucciones al
aparato ("recuérdame", "que me imprimas", "apúntame") **antes** de que el
modelo redacte, y las vuelve a pegar al final ya formateadas. El
recordatorio no pasa por el LLM, así que no puede salir deformado.

**Resultado:**

> *"Hoy has trabajado bien, has acabado la jornada intensa y has
> acumulado la sensación de quemado del curro. **No olvides: tienes que
> devolver las llaves.**"*

---

## Fase 10 — La RAM vuelve a cero

**El requisito:** la Pi se usa para más proyectos. Pichón no puede tener
5 GB residentes todo el día.

**Qué se hizo:** `keep_alive` de Ollama a 0, con una secuencia explícita:

```
petición → carga 3B → redacta → SUELTA 3B
                              → carga 7B → verifica → SUELTA 7B
         → finally: suelta ambos pase lo que pase
```

El `finally` importa: sin él, una excepción dejaría 5 GB ocupados.

**Medido:**

| | RAM antes | RAM después | Modelos cargados al acabar |
|---|---|---|---|
| Caso rápido | 7645 MB | 7566 MB | ninguno |
| Caso lento | 7577 MB | 7637 MB | ninguno |

Coste: el caso rápido pasa de 9 s a 42 s (cargar desde cero cada vez).
Es el compromiso correcto para una Pi compartida.

---

## De dónde salimos y dónde estamos

| | Al principio | Ahora |
|---|---|---|
| Día real | *"jugaste al tenis, al fútbol, al voleibol"* | trabajo, iglesia, las llaves |
| Ajedrez | *"te ganaste dos partidas"* | *"tu padre te ganó dos partidas"* |
| Recordatorio | *"es que te imprimas que tienes que devolver las llaves"* | *"No olvides: tienes que devolver las llaves"* |
| Longitud | cortada a media palabra | frase completa, ≤300 caracteres |
| RAM en reposo | 5 GB ocupados | 0 |

---

## Fase 11 — Whisper: atacar la transcripción

**El cuello de botella que quedaba.** Este es un dictado real, tal como
lo transcribió Chrome:

> *"hoy día vienen trabajo bien eh se ha acabado la jornada intensa...
> he tenido más tarde... lo voy a mala... la parroquia de San todo
> domingo"*

*"he tenido más tarde"* no significa nada. *"San todo domingo"* es "Santo
Domingo" partido en dos. El modelo trabaja bien sobre eso —copia en vez
de inventar, que es lo correcto— pero no puede arreglar lo que no se
entiende.

**Qué se hizo:**

- El navegador graba el audio con `MediaRecorder` **en paralelo** al
  reconocimiento de Chrome. Sigues viendo el texto al momento mientras
  hablas; el audio va aparte.
- Al guardar, se envían las dos cosas. La Pi convierte con `ffmpeg` a
  WAV 16 kHz mono y lo pasa por `whisper.cpp` con `-l es`.
- Si Whisper devuelve algo razonable (>20 caracteres), se usa. Si falla,
  se cae al texto de Chrome. `WHISPER_ACTIVO` comprueba que el binario y
  el modelo existan antes de intentarlo.
- El diario guarda ambas transcripciones, para poder compararlas.

**Medido en la Pi 5:** 11 s de audio en 7,4 s con el modelo `small` —
más rápido que tiempo real. Un dictado de 2 minutos tarda ~1,5 min en
transcribirse, y luego viene el destilado.

**Coste:** la espera total sube a 3-4 minutos con dictados largos. Para
un proceso nocturno en el que dictas y te vas a dormir, es asumible.

---

## Fase 12 — Tarea pendiente, no reproche

**El fallo:** el dictado decía *"y luego no olvidar de hablar con Juan
Carlos García"* — una tarea pendiente. El resumen salió:

> *"Luego **has olvidado** hablar con Ricardo Fuentes."*

Invierte el sentido, y encima suena a reproche en un papel que lees
recién levantado.

**Se planteó un modelo de razonamiento** (`qwen3:4b`, `deepseek-r1:7b`)
como capa de comprensión. Antes de descargar nada se midió la línea base
con los modelos ya instalados:

| Modelo | Aciertos distinguiendo tarea de hecho |
|---|---|
| `qwen2.5:7b-instruct` | **8/8** |
| `llama3.2:3b` | 4/8 (dice TAREA a todo: los aciertos son casualidad) |

**No hizo falta el razonador.** El 7B que ya estaba —el mismo que
verifica— resuelve la tarea. Las descargas se cancelaron.

Y había dos razones para desconfiar del razonador en esta máquina: el
código ya avisaba de que los modelos *thinking* a veces agotan los
tokens razonando y devuelven la respuesta vacía (`SIN_RAZONAR = True`),
y en una Pi sin GPU razonar cuesta cientos de tokens antes de la primera
palabra útil.

**Resultado:** 3/3 correctas. *"Mañana tienes que hablar con Ricardo
García"*.

---

## Fase 13 — Medir en vez de opinar

**El problema metodológico:** hasta aquí, casi todo se había medido
sobre **un solo dictado**. Eso arregla ese día y no dice nada de los
demás.

**20 ejecuciones del mismo texto** dieron 20/20 sin fallos, 3,6 cm de
papel, y una variabilidad pequeña (287-315 caracteres). Buenos números
—pero sobre el caso conocido.

**Ocho días nuevos**, ninguno parecido: dar clases de voleibol, una
operación de hombro, una mudanza, una discusión, aprobar una oposición,
un viaje a Lisboa, apuros de dinero, un día plano.

**Resultado: 4 de 8 con algún fallo.** Muy lejos del 20/20.

| Fallo | Ejemplo |
|---|---|
| Pierde detalles | la mudanza sin las cajas |
| Primera persona en presente | *"te dijo que **necesito** operarme"* |
| Gramática rota | *"has estado justo mes"* |
| Cambia un verbo | *"quiero ver el mirador"* → *"quieres hablar con la gente del mirador"* |

Ninguno es catastrófico —no inventa días enteros ni invierte quién gana
a quién— pero el sistema **está ajustado a un día concreto**.

**Y la batería encontró un fallo antes de ejecutarse.** La lista
`RASTROS_EJEMPLO` contenía `"jugaste al voleibol"`, puesto para cazar
el caso en que el modelo copiaba el ejemplo del prompt. Habría
rechazado una salida **correcta** el día que el autor diera clases de
voleibol. Se quitó.

> Cada arreglo específico es una hipótesis sobre el mundo. Hay que
> comprobarla contra casos que no la inspiraron.

---

## Fase 14 — El papel

Con `LARGO_TICKET = 380` los tickets salían a 3,6 cm. Bajarlo a **310**,
y pedir al modelo 280 caracteres en vez de 350:

| | 380 | 310 |
|---|---|---|
| Papel | 3,6 cm | **3,5 cm** |
| Caracteres | 287-315 | **288-301** |
| Líneas | 10-11 | **10 fijas** |
| Tiempo medio | 64 s | **38 s** |
| Fallos | 0/20 | **0/12** |

Lo interesante no es el papel —1 mm— sino que **el tiempo baja de 64 a
38 segundos**: al pedirle menos texto, el modelo acierta a la primera
más a menudo y el 7B se carga menos veces.

Con un rollo de 58 mm estándar salen unos 270 tickets: nueve meses.

---

## Lo que sigue abierto

**Generalizar a días que no hemos visto.** Es lo más importante. Sobre
el dictado conocido acierta 20/20; sobre ocho días nuevos, 4/8. Los
fallos están ordenados por facilidad de arreglo:

1. **Primera persona en presente** — *"necesito operarme"*.
   `corregir_persona()` cubre pretérito, futuro y parte del presente;
   faltan verbos. Determinista y barato.
2. **Pierde detalles** — la mudanza sin las cajas. Probablemente el
   límite de 310 caracteres apretando; habría que medirlo.
3. **Gramática rota ocasional** — *"has estado justo mes"*. Es el 3B
   tropezando; se atacaría con una candidata más o con un juez.

**Medir Whisper contra Chrome sobre dictados reales.** El diario guarda
las dos transcripciones (`texto` y `texto_navegador`). Las primeras
comparaciones son claras a favor de Whisper —*"Ricardo Fuentes"*
frente a *"San todo domingo"*— pero falta acumular días.

**El diccionario de nombres propios crece con el uso.** `Murcia` salía
como *"Burce"* hasta que se añadió al prompt de Whisper y a
`WHISPER_ARREGLOS`. Calles, barrios y nombres de compañeros irán
apareciendo.

---

## De dónde salimos

| | Al principio | Ahora |
|---|---|---|
| Día real | *"jugaste al tenis, al fútbol, al voleibol"* | trabajo, iglesia, las llaves |
| Ajedrez | *"te ganaste dos partidas"* | *"tu padre te ganó dos partidas"* |
| Recordatorio | *"es que te imprimas que tienes que devolver las llaves"* | *"No olvides: tienes que devolver las llaves"* |
| Tarea pendiente | *"has olvidado hablar con Ricardo"* | *"tienes que hablar con Ricardo"* |
| Transcripción | *"la parroquia de San todo domingo"* | *"el club de atletismo"* |
| Longitud | cortada a media palabra | 10 líneas, 3,5 cm |
| RAM en reposo | 5 GB ocupados | 0 |
