# Siete modelos, un banco de pruebas

Todos corriendo en la misma Raspberry Pi 5 (8 GB), vía Ollama, con el
mismo prompt y la misma temperatura (0.3). Las salidas son reales.

**Salvo la fila con harness:** esa genera tres candidatas a 0.25, 0.40
y 0.55, y se queda con la mejor. No es comparable directamente con las
demás, que son una sola generación a 0.3.

---

## El banco de pruebas

**Caso A — inversión de agente.** Gana la otra persona, no quien cuenta.
Es el caso que ningún modelo resolvió solo.

> *"por la tarde jugué al ajedrez con mi padre y me ganó dos partidas
> seguidas, luego mi hermana nos preparó la cena"*

Correcto: *"tu padre te ganó"*. Incorrecto: *"te ganaste"*, *"ganaste"*,
*"le ganaste"*.

**Caso B — dictado real ambiguo.** Habla roto, con muletillas y una
petición dirigida al aparato.

> *"bueno primer día de trabajo hoy sin el área intensivo aquí seguimos
> bien concentrado hemos llegado bastante de curro sí contento... y
> recuérdame porfa que lo más importante para hoy es devolver las llaves"*

Correcto: recoge trabajo, iglesia y las llaves sin inventar actividades.

---

## Resultados

Ocho filas, siete modelos: `llama3.2:3b` aparece dos veces, con el
harness y sin él.

| Modelo | Tamaño | Caso A | Caso B | Velocidad | Veredicto |
|---|---|---|---|---|---|
| **`llama3.2:3b` + harness** | 2.0 GB | bien, 4/4 | bien | 9-22 s | **el elegido** |
| `llama3.2:3b` solo | 2.0 GB | falla | regular | 9-22 s | mejor base |
| `qwen2.5:7b-instruct` | 4.7 GB | falla, copia el original | regular | 29-123 s | **buen verificador** |
| `llama3.1:8b` | 4.9 GB | invierte el resultado | inventa | 24-40 s | descartado |
| `mistral:7b` | 4.4 GB | invierte el resultado | 1ª persona plural | 20-47 s | descartado |
| `glm4:9b` | 5.5 GB | falla | — | 322 s | descartado |
| `gemma2:2b` | 1.6 GB | falla | inventa | 6-15 s | descartado |
| `phi3:3.8b` | 2.2 GB | invierte el resultado | español roto | 11-23 s | descartado |

---

## Las salidas, una por una

### `llama3.2:3b` + harness — el elegido

```
Caso A: "Hoy por la tarde jugaste al ajedrez con tu padre y tu padre te
         ganó dos partidas seguidas, luego tu hermana preparó la cena."
        4 de 4 intentos correctos

Caso B: "Hoy has trabajado bien, has acabado la jornada intensa y has
         acumulado la sensación de quemado del curro.
         No olvides: tienes que devolver las llaves."
```

Sin el harness, el mismo modelo escribía *"te ganaste dos partidas"* y
*"lo saqué"* en primera persona.

---

### `qwen2.5:7b-instruct` — mal redactor, excelente juez

```
Caso B: "Hoy me levantaste tarde fatal, luego jugaste al tenis con Clara
         y me ha ganado ella 6-3, estuve toda la tarde con el conector..."
```

Mezcla primera y segunda persona en la misma frase y apenas reescribe:
copia el dictado cambiando algún verbo suelto. Probado a temperatura
0.1, 0.3 y 0.5 — idéntico, así que no es aleatoriedad sino sesgo.

**Pero verificando acierta 6/6:**

| Afirmación | Esperado | Respondió |
|---|---|---|
| "jugaste al ajedrez con tu padre" | SÍ | SÍ |
| "tu hermana preparó la cena" | SÍ | SÍ |
| "tu padre te ganó dos partidas" | SÍ | SÍ |
| "fuiste a la playa con tus primos" | NO | NO |
| "comiste paella mirando el mar" | NO | NO |
| "estudiaste para el examen" | NO | NO |

Su rigidez es defecto redactando y virtud juzgando. **Es el verificador
del sistema.**

---

### `llama3.1:8b` — invierte y alucina

```
Caso A: "Hoy jugaste al ajedrez con tu padre y ganaste dos partidas
         seguidas. Luego, tu hermana te preparó la cena. No olvides que
         mañana tienes que estudiar para el examen."
```

Invierte el resultado **y** se inventa un examen que nunca se mencionó.
En otra ejecución añadió *"traer tus libros"*.

---

### `mistral:7b` — cambia de persona y de tiempo

```
Caso A: "Tú jugaste ajedrez con tu padre y le ganaste dos partidas
         seguidas. Mañana recuerda que tu hermana preparará la cena."

Caso B: "Te recordamos que el primer día de trabajo sin el área intensivo
         fue exitoso. Aquí, mantuvimos una buena concentración.
         Llegamos bastante de curso y estamos contentos."
```

Invierte el resultado, mueve la cena al futuro, y en el caso B se pasa a
primera persona del plural con un tono corporativo raro.

---

### `glm4:9b` — lento y sin discriminación

322 segundos por respuesta. Verificando saca 3/6: dice "NO" a todo,
igual que el 3B. Descartado por ambas razones.

---

### `gemma2:2b` — inventa y mezcla tratamientos

```
Caso A: "Tu padre jugaste al ajedrez con usted por la tarde y usted ganó
         dos partidas seguidas."

Caso B: "Llegaste a la oficina sin el área intensivo, y te sentaste bien."
```

*"te sentaste bien"* no está en el dictado. Y mezcla tuteo con usted en
la misma frase.

---

### `phi3:3.8b` — el peor en español

```
Caso A: "Ayer por la tarde jugaste al ajedrez con tu padre y ganaste dos
         partidas seguidas."

Caso B: "Hiciste bien en mantener la concentra extranjera, llegaste bien
         y te sentiste bien."
```

*"la concentra extranjera"* no significa nada. En otra salida escribió
*"llegó bastante de Curro"*, tratando "curro" como nombre propio. Poco
entrenamiento en español.

---

## Lo que enseñan estos números

**El tamaño no predijo la calidad.** El mejor redactor de los siete es el
segundo más pequeño. El de 9B fue el más lento y de los peores.

**Todos fallan el mismo caso.** Invertir el agente al cambiar de persona
verbal es difícil para modelos de este rango, y no se arregla con
parámetros. Se arregló con 40 líneas de regex.

**Cada tarea quiere su modelo.** El que redacta y el que verifica no
tienen por qué ser el mismo — y aquí, deliberadamente, no lo son.

**La velocidad importa más de lo que parece.** El 3B es 3× más rápido que
los 8B. En un sistema que carga y descarga modelos por petición, eso
decide si el usuario espera 40 segundos o dos minutos.

---

## Notas de reproducibilidad

- Raspberry Pi 5, 8 GB, sin swap, Raspberry Pi OS.
- Ollama, cuantización por defecto (Q4_K_M en todos).
- Temperatura 0.3 salvo donde se indica; `num_predict` 200.
- **Aviso sobre las medidas de tiempo:** la primera llamada a un modelo
  incluye la carga desde disco (~30-120 s según tamaño). Los rangos de
  la tabla van de "ya cargado" a "carga en frío".
- Un proceso `llama-server` huérfano puede retener GB sin que `ollama ps`
  lo muestre. Si un modelo falla con `KeyError: 'response'`, es falta de
  memoria: `sudo systemctl restart ollama`.
