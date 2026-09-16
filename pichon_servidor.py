#!/usr/bin/env python3
"""
PICHON DIARIO — el cerebro (corre en la Raspberry Pi)
==================================================================
De noche le hablas al movil: cuentas como ha ido el dia.
El LLM local destila una MORALEJA corta.
Por la mañana el ESP32 pide el ticket y lo imprime.

  NOCHE   movil (voz, HTTPS) -> Pi -> Ollama -> guarda la moraleja
  MAÑANA  ESP32 -> GET /ticket -> frase del libro + moraleja -> papel

Instalar en la Pi:
    sudo apt install python3-flask python3-requests -y
    (Ollama y llama3.2:3b ya los tienes)

Arrancar:
    python3 pichon_servidor.py https
    -> movil (Chrome): https://IP_DE_LA_PI:5000   (acepta el certificado)
    La IP:  hostname -I

Para que arranque solo al encender la Pi, mira el final del archivo.
==================================================================
"""
from flask import Flask, request, jsonify, Response
from datetime import datetime, timedelta
import json, os, requests, subprocess, tempfile, time

# --- Motor del LLM ---
# "llamacpp" -> llama-server (recomendado en Termux/Android)
# "ollama"   -> Ollama, si lo tienes funcionando
MOTOR   = "ollama"
URL_LLAMACPP = "http://127.0.0.1:8080/v1/chat/completions"

# Los modelos de la generacion "thinking" (Qwen3, Qwen3.5...) razonan
# antes de responder y a veces agotan los tokens sin emitir respuesta:
# devuelven content vacio y todo en reasoning_content. En un proceso
# nocturno desatendido eso es fatal, asi que lo desactivamos.
SIN_RAZONAR = True
URL_OLLAMA   = "http://127.0.0.1:11434/api/generate"
MODELO_OLLAMA = "llama3.2:3b"

# Modelo para VERIFICAR, que es otra tarea distinta de redactar.
# Probados 7 modelos (2B a 9B): redactando, el mejor con diferencia es
# llama3.2:3b. Pero verificando ("esta esto dicho en el texto? SI/NO")
# el 3B contesta NO a todo, y tambien glm4:9b. qwen2.5:7b acierta 6/6,
# justo porque se pega al texto original: mal redactor, buen juez.
# Cuesta ~20 s por frase, asi que solo se usa cuando la nota deja dudas.
MODELO_VERIFICADOR = "qwen2.5:7b-instruct"
VERIFICAR_ACTIVO = True

# --- RAM: cargar solo mientras se usa ---
# La Pi se comparte con otros proyectos, asi que Pichon no puede tener
# modelos residentes. Ollama los deja 5 minutos en memoria por defecto;
# con esto los soltamos en cuanto terminan.
#
#   keep_alive = 0      -> descarga en cuanto responde
#   keep_alive = "5m"   -> se queda 5 minutos (defecto de Ollama)
#
# La secuencia de una peticion es: carga 3B -> redacta -> suelta 3B ->
# carga 7B -> verifica todas las frases -> suelta 7B -> RAM a cero.
# Nunca hay dos modelos a la vez: el pico es un solo modelo.
KEEP_ALIVE_TRABAJANDO = "5m"   # mientras el modelo tiene tarea pendiente
KEEP_ALIVE_AL_TERMINAR = 0     # 0 = sueltalo ya

# --- Transcripcion con Whisper ---
# El reconocimiento de Chrome es la mayor fuente de error del sistema:
# con habla rapida devuelve cosas como "he tenido mas tarde" o parte
# "Santo Domingo" en "San todo domingo". El modelo trabaja bien sobre
# texto roto (copia en vez de inventar), pero no puede arreglar lo que
# no se entiende.
#
# whisper.cpp con el modelo "small" transcribe mas rapido que tiempo
# real en la Pi 5: 11 s de audio en 7,4 s. El navegador manda el audio
# y el texto de Chrome; si Whisper responde, usamos el suyo.
WHISPER_BIN = os.path.expanduser("~/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODELO = os.path.expanduser("~/whisper.cpp/models/ggml-small.bin")
WHISPER_ACTIVO = os.path.exists(WHISPER_BIN) and os.path.exists(WHISPER_MODELO)

# Nombres propios que sueles decir. Whisper acepta un prompt inicial
# que le sesga el vocabulario: sin esto convirtio "Murcia" en "Burce".
# Anade aqui los tuyos: sitios, personas, calles, el barrio.
WHISPER_CONTEXTO = (
    "Diario personal en espanol de Espana. Pueden aparecer estos "
    "nombres: Murcia, Santo Domingo, Juan Carlos Garcia, Ramon, Marta."
)

# Correcciones fijas para lo que Whisper sigue entendiendo mal. Es la
# red de seguridad del prompt de arriba: clave = lo que oye,
# valor = lo que era. Se aplican como palabra completa.
WHISPER_ARREGLOS = {
    "Burce": "Murcia",
    "Burcia": "Murcia",
    "Murce": "Murcia",
}
# Margen amplio: un dictado de 3 minutos son ~2 min de transcripcion.
TIMEOUT_WHISPER = 420

# El destilado completo puede tardar: 3 candidatas con el 3B, mas la
# verificacion con el 7B, mas las cargas y descargas de ambos. Medido:
# ~200 s en el caso lento SIN descargar, y las cargas anaden ~40 s por
# modelo. 600 s deja margen para un dictado largo.
TIMEOUT_LLM = 600

# Cuanto puede ocupar el parrafo del ticket, en caracteres.
# A 32 caracteres por linea en la termica de 58 mm, 310 son ~10 lineas,
# unos 3,5 cm de papel.
#
# Historia de este numero: estaba en 300, se subio a 380 porque
# recortar_a_frase() corta por el FINAL y se perdio entera una frase
# sobre con quien habia que hablar. Medido sobre 20 ejecuciones, las
# salidas se quedaban en 287-315 caracteres, asi que 310 es el tope
# justo por encima de lo que el modelo produce de forma natural: no
# corta casi nunca, pero pone techo.
LARGO_TICKET = 310

# --- Que imprime el ticket ---
# "sintesis"  -> reescribe los hechos como un parrafo natural. Es el
#                punto medio: comprimir y redactar, sin moralizar.
#                RECOMENDADO: es lo que un modelo pequeno puede hacer.
# "hechos"    -> devuelve los hechos tal cual, sin redactar. A prueba
#                de fallos: no puede alucinar porque no genera nada.
# "reflexion" -> ademas intenta una frase de cierre con sentido.
#                Necesita un modelo mas capaz que un 0.5B.
MODO = "sintesis"

# --- Cuantas pasadas al LLM ---
# 1 -> el modelo lee el texto entero y sintetiza. Mejor resultado y mas
#      rapido, pero necesita un modelo capaz (3B o mas). RECOMENDADO en la Pi.
# 2 -> extrae hechos y luego redacta. Muleta para modelos muy pequenos.
PASADAS = 1
DIARIO  = os.path.expanduser("~/pichon_diario.json")   # tus entradas
FRASES  = os.path.expanduser("~/pichon_frases.txt")    # una frase por linea

# Cuantas horas atras cuenta como "anoche" cuando el ESP32 pide el ticket
VENTANA_HORAS = 18

app = Flask(__name__)

# ---------------- ALMACEN ----------------
def cargar():
    if not os.path.exists(DIARIO):
        return []
    try:
        with open(DIARIO) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

def guardar(entradas):
    with open(DIARIO, "w") as f:
        json.dump(entradas, f, ensure_ascii=False, indent=1)

def frases():
    """Las frases del libro, una por linea. Lineas vacias y # se ignoran."""
    if not os.path.exists(FRASES):
        return ["Anota tus frases en pichon_frases.txt"]
    with open(FRASES, encoding="utf-8") as f:
        out = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    return out or ["El archivo de frases esta vacio"]

def sin_tildes(s):
    """La impresora usa otra tabla de caracteres: quitamos acentos."""
    tabla = str.maketrans("áàäâéèëêíìïîóòöôúùüûñçÁÀÄÂÉÈËÊÍÌÏÎÓÒÖÔÚÙÜÛÑÇ",
                          "aaaaeeeeiiiioooouuuuncAAAAEEEEIIIIOOOOUUUUNC")
    return s.translate(tabla).replace("¿", "").replace("¡", "")

# ---------------- EL LLM ----------------
import re

# ------------------------------------------------------------------
# LIMPIEZA: lo que puede hacer un regex no se lo pedimos a la red.
# Quitar muletillas y repeticiones le ahorra al modelo medio trabajo.
# ------------------------------------------------------------------
MULETILLAS = [
    r"\b(eh+|em+|mmm+|este+)\b",
    r"\b(o sea|osea)\b",
    r"\b(sabes|¿sabes\?|no se|no sé)\b",
    r"\b(digamos|bueno pues|pues nada|en plan)\b",
    r"\b(este|esto) es que\b",
]

def limpiar(texto):
    t = " " + texto.strip() + " "
    for p in MULETILLAS:
        t = re.sub(p, " ", t, flags=re.IGNORECASE)
    t = re.sub(r"\b(\w+)( \1\b)+", r"\1", t, flags=re.IGNORECASE)   # "y y y" -> "y"
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    return t.strip()

def trocear(texto, palabras=200):
    """Parte el texto en trozos que quepan holgados en el contexto."""
    ps = texto.split()
    return [" ".join(ps[i:i+palabras]) for i in range(0, len(ps), palabras)] or [texto]

# ------------------------------------------------------------------
# PASADA 1 — EXTRAER. Es lo que mejor hace un modelo pequeno.
# ------------------------------------------------------------------
P_EXTRAER = """Lee este fragmento de alguien contando su dia y escribe
los hechos importantes Y COMO SE SINTIO, uno por linea, empezando cada
uno con un guion. Copia lo que dijo, no lo interpretes ni anadas nada.
Maximo 5 lineas. Responde solo las lineas.

Ejemplo de entrada:
pues hoy me levante tarde y estuve toda la manana con el conector de la impresora que estaba mal puesto al final lo saque estoy contento pero agotado

Ejemplo de salida:
- Se levanto tarde.
- Paso la manana peleando con el conector de la impresora.
- Estaba mal puesto y al final lo resolvio.
- Se siente contento pero agotado.

Fragmento:
{texto}
"""

# ------------------------------------------------------------------
# PASADA 2 — ABSTRAER, pero ya sobre texto corto y limpio.
# ------------------------------------------------------------------
# El ejemplo debe ensenar el FORMATO, nunca el contenido. Un modelo
# pequeno, si el ejemplo es bueno y concreto, lo copia tal cual en vez
# de resolver la tarea. Por eso va en tercera persona y con hechos
# anodinos que no se pueden confundir con un dia real.
P_DIRECTO = """Alguien te ha contado como le ha ido el dia. Reescribelo
como un parrafo corto para que lo lea manana en un ticket de papel.

Reglas estrictas:
- Dirigete a esa persona de TU. TODOS los verbos de lo que hizo van en
  segunda persona del pasado, terminados en -aste o -iste. Prohibido
  usar "yo", "me", "mi", o verbos acabados en -e como si hablaras tu.
- Usa SOLO las cosas que aparecen en el texto de abajo. Cada actividad
  que menciones tiene que estar escrita literalmente ahi. Si no lo has
  leido en ese texto, NO lo escribas.
- Si te pidio recordar algo para manana, eso va SIEMPRE en el parrafo.
- Respeta QUIEN hace cada cosa. Si alguien le gano, no digas que gano el.
- No des consejos, no saques lecciones, no moralices.
- REESCRIBE, no copies. Junta lo que te conto en frases tuyas, en
  orden y con sentido. Si una frase del texto esta a medias o no se
  entiende, no la arrastres: quitala.
- Nada de "hemos", "estamos", "me": el que lee es el que te lo conto.
- Breve: dos o tres frases, 280 caracteres maximo. Va en un ticket de
  papel: cada frase de mas son dos centimetros.
- Pero no te dejes nada importante por acortar: si te conto con quien
  tiene que hablar, o adonde tiene que ir, eso entra. Antes que quitar
  eso, quita adjetivos y rodeos.
- No repitas la misma frase dos veces.
- En espanol. Responde solo el parrafo, sin comillas ni preambulo.

Lo que te ha contado:
{texto}

Parrafo:"""

P_SINTESIS = """Reescribe estos hechos como un parrafo corto, en segunda
persona, como si le contaras a alguien como fue su dia.

Reglas estrictas:
- Usa SOLO la informacion de la lista. No anadas nada.
- No des consejos, no saques lecciones, no moralices.
- Une los hechos con naturalidad, no los enumeres.
- Dos o tres frases. Maximo 220 caracteres.
- Responde solo el parrafo.

Hechos:
{hechos}

Parrafo:"""

P_MORALEJA = """Estos son los hechos del dia de una persona.
Escribe DOS frases cortas sobre lo que le ha pasado HOY, usando solo
los hechos de la lista. Habla de TU (segunda persona).
Prohibido inventar nada que no este en la lista.
Maximo 200 caracteres. Responde solo el texto, sin comillas.

--- EJEMPLO (solo para el formato, NO uses su contenido) ---
Hechos:
- Ana fue al mercado.
- Compro fruta.
- Volvio andando.
- Se siente tranquila.

Respuesta:
Hoy fuiste al mercado y volviste andando con la fruta. Un dia sencillo, y te dejo tranquilo.
--- FIN DEL EJEMPLO ---

Ahora hazlo con estos hechos REALES:
{hechos}

Respuesta:"""

# Trozos del ejemplo: si aparecen en la respuesta, el modelo lo ha copiado
RASTROS_EJEMPLO = ["ana fue al mercado", "compro fruta", "volviste andando",
                   "cable del reves", "no rendirte antes"]

# Ojo con anadir aqui cosas genericas. Estuvieron "jugaste al tenis",
# "jugaste al futbol" y "jugaste al voleibol" para cazar un caso en que
# el modelo copiaba el ejemplo del prompt. Pero eso rompe el dia en que
# de verdad juegas al voleibol: el sistema rechazaria una salida
# correcta. El ejemplo ya no esta en el prompt, y validar_directo() caza
# lo inventado comparando vocabulario con el texto, que es la via buena:
# no depende de una lista de palabras prohibidas.

def copio_el_ejemplo(m):
    b = m.lower()
    return any(r in b for r in RASTROS_EJEMPLO)

def llamar(prompt, max_tokens=150, temp=0.25, modelo=None, keep_alive=None):
    """Una llamada al LLM local. Devuelve texto o None.

    `modelo` permite usar otro distinto del de redaccion: la
    verificacion la hace un 7B, que es mejor juez aunque peor
    redactor.
    """
    try:
        if MOTOR == "llamacpp":
            cuerpo = {
                "messages": [{"role": "user", "content": prompt}],
                "temperature": temp,
                "max_tokens": max_tokens,
            }
            if SIN_RAZONAR:
                # llama.cpp acepta estos dos; los modelos sin modo
                # thinking simplemente los ignoran.
                cuerpo["chat_template_kwargs"] = {"enable_thinking": False}
                cuerpo["reasoning_format"] = "none"

            r = requests.post(URL_LLAMACPP, json=cuerpo, timeout=TIMEOUT_LLM)
            msg = r.json()["choices"][0]["message"]
            texto = (msg.get("content") or "").strip()
            if not texto:
                # Se quedo razonando y no emitio respuesta: rescatamos
                # lo ultimo del razonamiento antes que devolver nada.
                razon = (msg.get("reasoning_content") or "").strip()
                if razon:
                    print("  aviso: respondio solo en reasoning_content")
                    texto = razon.split("\n")[-1].strip()
            return texto
        cuerpo = {
            "model": modelo or MODELO_OLLAMA, "prompt": prompt, "stream": False,
            "options": {"temperature": temp, "num_predict": max_tokens},
            # Cuanto se queda el modelo en RAM tras responder. Por
            # defecto lo dejamos cargado mientras quedan llamadas; quien
            # sabe que ha terminado pasa KEEP_ALIVE_AL_TERMINAR.
            "keep_alive": KEEP_ALIVE_TRABAJANDO if keep_alive is None else keep_alive,
        }
        r = requests.post(URL_OLLAMA, json=cuerpo, timeout=TIMEOUT_LLM)
        return r.json()["response"].strip()
    except Exception as e:
        print("  el LLM fallo:", e)
        return None

def validar_directo(m, original):
    """Rechaza la salida de una pasada si se ha inventado el dia.

    Nacio de un caso real: el modelo copio el ejemplo del prompt y
    escribio "Hoy jugaste al tenis, hoy jugaste al futbol, hoy jugaste
    al voleibol" sobre un dia que hablaba de trabajo y curriculums.
    Sin este filtro la salida se guardaba tal cual.
    """
    t = m.strip()
    if len(t) < 30:
        return False

    # 0) Palabras inventadas. El 3B fabrica engendros como "saquéste" o
    #    "juegaste" al forzar la segunda persona. No existe diccionario
    #    aqui, pero estas dos formas son imposibles en espanol:
    #      - acento en medio de palabra seguido de mas letras
    #      - dos terminaciones de persona pegadas (-asteste, -isteste)
    if re.search(r"\w[áéíóú]\w*(?:ste|steis)\b", t):
        return False
    if re.search(r"\b\w+(?:aste|iste)(?:ste|s?te)\b", t):
        return False

    # 1) Frases repetidas: el sintoma mas claro de que entro en bucle.
    frases = [f.strip().lower() for f in re.split(r"[.;]", t) if f.strip()]
    if len(frases) != len(set(frases)):
        return False

    # 2) Vocabulario inventado. Las palabras largas de la salida deben
    #    venir del texto original; si mas de un tercio son nuevas, se
    #    lo esta inventando. Comparamos sin tildes para que "futbol" y
    #    "fútbol" cuenten igual.
    VACIAS = {"hoy", "pero", "aunque", "porque", "cuando", "donde",
              "estas", "estuviste", "fuiste", "tuviste", "hiciste",
              "para", "como", "mientras", "luego", "despues", "tambien",
              "sientes", "siente", "manana", "sobre", "hasta", "desde",
              "entre", "muy", "mas", "menos", "todo", "toda", "poco"}

    def palabras(s):
        s = sin_tildes(s.lower())
        return {w.strip(".,;:!?\"'") for w in s.split() if len(w) > 4}

    orig = palabras(original)
    sal = palabras(t) - VACIAS
    if not sal:
        return False

    # Una palabra vale si aparece en el original o si comparte raiz con
    # alguna de alli. Comparamos por los 4 primeros caracteres y ademas
    # quitando la terminacion verbal: al conjugar, la raiz cambia
    # ("jugue" -> "jugaste", "saque" -> "sacaste"), y con un prefijo
    # largo el validador acababa castigando justo las conjugaciones
    # BIEN hechas.
    TERMINACIONES = ("aste", "iste", "amos", "imos", "aron", "ieron",
                     "ado", "ido", "ando", "iendo", "are", "ere", "ira")

    def raiz(w):
        for t in TERMINACIONES:
            if w.endswith(t) and len(w) - len(t) >= 3:
                return w[:-len(t)]
        return w

    raices = {raiz(o)[:4] for o in orig}

    def conocida(w):
        if w in orig:
            return True
        r = raiz(w)[:4]
        if r in raices:
            return True
        # "saque"/"sacaste" comparten solo 3: comparamos tambien asi.
        return any(r[:3] == x[:3] for x in raices if len(x) >= 3)

    nuevas = [w for w in sal if not conocida(w)]
    if len(nuevas) > len(sal) / 3:
        return False

    return True


def validar_sintesis(m, hechos):
    """Rechaza salidas que no son un parrafo util."""
    t = m.strip()
    # El tope va alineado con LARGO_TICKET: si el validador acepta mas
    # de lo que cabe, el recorte posterior se come frases enteras.
    if len(t) < 30 or len(t) > LARGO_TICKET + 40:
        return False
    if t.count("\n") > 2 or t.lstrip().startswith(("-", "*", "1.")):
        return False                       # sigue siendo una lista
    if "hechos:" in t.lower() or "parrafo:" in t.lower():
        return False                       # ha copiado el prompt
    # que al menos comparta vocabulario con los hechos (no se lo invento)
    pal_h = set(w.lower() for h in hechos for w in h.split() if len(w) > 4)
    pal_m = set(w.lower().strip(".,;:") for w in t.split() if len(w) > 4)
    if pal_h and len(pal_h & pal_m) < max(1, len(pal_h) // 6):
        return False
    return True


# ==================================================================
# LO QUE LE PIDES A PICHON, APARTE DEL RELATO
# ==================================================================
# Como sueles pedirle cosas al aparato. Todo lo que venga detras es el
# recordatorio; lo de delante es relato.
PETICIONES = [
    r"recu[ée]rdame (?:porfa |por favor |que )?",
    r"(?:que )?me imprimas (?:que )?",
    r"(?:que )?me apuntes (?:que )?",
    r"(?:que )?me recuerdes (?:que )?",
    r"ap[uú]ntame (?:que )?",
    r"no (?:se )?me olvide (?:que )?",
    r"acu[ée]rdate de (?:que )?",
]

# Frases que marcan que viene lo importante del dia siguiente.
PREFIJOS = [
    r"lo m[aá]s importante (?:para (?:hoy|ma[nñ]ana) )?es (?:que )?",
    r"lo importante (?:es )?(?:que )?",
]


def _limpiar_resto(t):
    t = re.sub(r"^\s*(?:que|de|y|,)\s+", "", t.strip())
    return t.strip(" ,.;")


def extraer_recordatorios(texto):
    """Devuelve (texto_sin_peticiones, [recordatorios]).

    El texto que vuelve es el relato del dia limpio de instrucciones
    al aparato; los recordatorios van aparte, tal cual los dijiste.
    """
    recordatorios = []
    t = texto

    patron = r"(?:%s)(?:%s)?" % ("|".join(PREFIJOS + PETICIONES),
                                 "|".join(PETICIONES))

    while True:
        m = re.search(patron, t, flags=re.IGNORECASE)
        if not m:
            break
        # Todo lo que va desde la peticion hasta el siguiente corte
        # fuerte es el recordatorio.
        resto = t[m.end():]
        # Un dictado por voz no trae puntuacion, asi que el corte lo
        # marcan las muletillas con que se cambia de tema. Sin coma
        # obligatoria: "...echar CVs por lo demas bien..." va seguido.
        # Un dictado por voz no trae puntuacion, asi que el corte lo
        # marcan las muletillas con que se cambia de tema, o la
        # siguiente peticion si encadenaste varias.
        corte = re.search(r"\.|\b(?:por lo dem[aá]s|luego|despu[eé]s|"
                          r"no olvidar|no olvides|adem[aá]s|"
                          r"y (?:tambi[eé]n|luego)|aparte)\b"
                          r"|(?:%s)" % "|".join(PETICIONES), resto,
                          flags=re.IGNORECASE)
        fin = corte.start() if corte else len(resto)
        cuerpo = _limpiar_resto(resto[:fin])
        if len(cuerpo) > 3:
            recordatorios.append(cuerpo)
        t = t[:m.start()] + " " + resto[fin:]
        t = re.sub(r"\s+", " ", t)

    # Al sacar la peticion queda el arranque de su frase colgando:
    #   "Por favor, es que me recuerdes de X. Por favor."
    #   -> "Por favor, es . Por favor."
    # El modelo copiaba ese muñon tal cual al ticket. Barremos las
    # frases que se quedaron sin contenido.
    t = re.sub(r"\s+([,.;:])", r"\1", t)
    frases = re.split(r"(?<=[.!?])\s+", t)
    utiles = []
    for f in frases:
        desnuda = re.sub(r"[^\w\s]", "", f).strip()
        # Sin verbo ni sustantivo propio, una frase de menos de tres
        # palabras utiles no dice nada: "Por favor.", "es.", "Y luego."
        palabras = [w for w in desnuda.split() if len(w) > 2]
        if len(palabras) >= 3:
            utiles.append(f.strip())
    t = " ".join(utiles) if utiles else t

    return t.strip(" ,.;"), recordatorios


def con_recordatorio(parrafo, recordatorios):
    """Pega el recordatorio al final del parrafo del dia.

    Va aparte a proposito: el recordatorio es lo que pediste que no se
    te olvidara, asi que entra SIEMPRE, aunque haya que recortar el
    relato para que quepa en el ticket.
    """
    extra = formatear_recordatorio(recordatorios)
    if not extra:
        return parrafo
    if not parrafo:
        return extra

    # Ojo con recortar: recortar_a_frase() corta POR EL FINAL, y al
    # contar el dia lo que va al final suele ser lo que mas importa
    # ("no olvidar hablar con Juan Carlos Garcia..."). Perdimos esa
    # frase entera por apurar el limite del ticket.
    #
    # Asi que el limite se estira: 2 cm mas de papel valen mucho menos
    # que una frase perdida. Solo recortamos si el resultado se va de
    # verdad, y avisamos en el log cuando pasa.
    junto = parrafo.rstrip() + " " + extra
    if len(junto) <= LARGO_TICKET + 40:
        return junto

    hueco = LARGO_TICKET + 40 - len(extra) - 1
    print(f"  aviso: {len(junto)} caracteres, recorto el relato a {hueco}")
    return recortar_a_frase(parrafo, hueco) + " " + extra


def formatear_recordatorio(rs):
    """Convierte los recordatorios en una frase para el ticket."""
    if not rs:
        return ""
    # Pasamos a segunda persona lo minimo: "tengo que" -> "tienes que".
    limpios = []
    for r in rs:
        r = re.sub(r"\btengo que\b", "tienes que", r, flags=re.IGNORECASE)
        r = re.sub(r"\bdebo\b", "debes", r, flags=re.IGNORECASE)
        r = re.sub(r"\bvoy a\b", "vas a", r, flags=re.IGNORECASE)
        r = re.sub(r"\bmi\b", "tu", r, flags=re.IGNORECASE)
        # Al cortar en la siguiente peticion puede quedar un "y" o un
        # "que" colgando al final.
        r = re.sub(r"\s+(?:y|que|de|a)\s*$", "", r.strip(" ,.;"),
                   flags=re.IGNORECASE)
        if r:
            limpios.append(r)
    if not limpios:
        return ""
    if len(limpios) == 1:
        return "No olvides: " + limpios[0] + "."
    return "No olvides: " + "; ".join(limpios) + "."


# ==================================================================
# QUIEN GANA A QUIEN
# ==================================================================
# El 3B acierta SIEMPRE cuando el sujeto va delante y explicito
# ("ella te gano 6-3") y falla cuando esta implicito ("me gano dos
# partidas"): escribe "te ganaste", que invierte el resultado.
# Probado con 6 variantes de prompt, ninguna lo arregla. Asi que
# actuamos por los dos lados: explicitamos el sujeto ANTES de que el
# modelo lea el texto, y arreglamos la concordancia DESPUES.

_ACENTOS = str.maketrans("áéíóúñÁÉÍÓÚ", "aeiounAEIOU")


def _sin_acentos(s):
    return s.translate(_ACENTOS)


# Verbos de competicion, simples y compuestos ("me ha ganado").
VERBO = r"(?:ha |han |habia )?(?:gano|ganó|ganado|vencio|venció|vencido|"
VERBO += r"batio|batió|batido|derroto|derrotó|derrotado)"

# Parentescos y roles con que se nombra al rival en un dictado.
PARENTESCO = (r"(?:mi |el |la )?(?:padre|madre|hermano|hermana|primo|prima|"
              r"amigo|amiga|jefe|jefa|novio|novia|companero|companera|"
              r"rival|contrincante|equipo)")

# Nombre propio: mayuscula inicial y al menos 3 letras, para no tragarse
# "Dos" ni el comienzo de una frase.
NOMBRE = r"[A-ZÁÉÍÓÚ][a-záéíóúñ]{2,}"

# Pronombres que pueden ser el sujeto pospuesto.
PRONOMBRE = r"(?:ella|el|él|ellos|ellas)"

RIVAL = r"(?:%s|%s|%s)" % (PARENTESCO, NOMBRE, PRONOMBRE)


def explicitar_sujeto(texto):
    """Reescribe para que el ganador quede delante del verbo.

    Dos formas, en este orden (importa: la primera es mas especifica).
    """
    t = texto

    # 1) El sujeto ya esta, pero DETRAS del verbo, que es lo que
    #    despista al modelo:  "me gano ella"  ->  "ella me gano"
    def mover(m):
        return "%s me %s" % (m.group(2), m.group(1))

    # Sin IGNORECASE en el sujeto: con el, "[A-Z][a-z]{2,}" tambien
    # casaba con "dos" y salia "me gano dos partidas" -> "dos me gano".
    # (?i:...) aplica el ignorecase solo al verbo, que si lo necesita.
    t = re.sub(r"\bme ((?i:" + VERBO + r")) (" + RIVAL + r")\b",
               mover, t)

    # 2) No hay sujeto en la frase del verbo, pero si aparece antes
    #    como "con X". Lo repetimos para que quede explicito:
    #      "jugue con mi padre y me gano dos partidas"
    #      -> "jugue con mi padre y mi padre me gano dos partidas"
    #    Solo si entre "con X" y "me gano" no hay ya otro sujeto.
    def repetir(m):
        rival, medio, verbo = m.group(1), m.group(2), m.group(3)
        return "con %s%s y %s me %s" % (rival, medio, rival, verbo)

    t = re.sub(r"\bcon (" + RIVAL + r")([^.]{0,40}?) y me (" + VERBO + r")",
               repetir, t, flags=re.IGNORECASE)

    return t


# Sujeto en tercera persona + verbo en segunda: "el te ganaste".
# Es agramatical en espanol, asi que cuando aparece sabemos con
# certeza que el modelo conjugo mal. Pasamos el verbo a tercera.
SUJ3 = (r"(?:\u00e9l|ella|ellos|ellas|"
        r"(?:mi |tu |su |el |la )?(?:padre|madre|hermano|hermana|primo|"
        r"prima|amigo|amiga|jefe|jefa|novio|novia|companero|companera|"
        r"rival|contrincante|equipo)|"
        r"[A-Z\u00c1\u00c9\u00cd\u00d3\u00da][a-z\u00e1\u00e9\u00ed\u00f3\u00fa\u00f1]{2,})")


def arreglar_concordancia(texto):
    """Corrige "el te ganaste" -> "el te gano".

    Cuando el sujeto es de tercera persona, el verbo no puede ir en
    -aste/-iste. Deshacemos la terminacion:
        ganaste  -> gano     (-aste -> -o)
        perdiste -> perdio   (-iste -> -io)
    """
    # Palabras con mayuscula que NO son sujetos: adverbios de tiempo y
    # conectores que abren frase. Sin esto, "Hoy comiste" se toma como
    # sujeto "Hoy" + verbo, y sale "Hoy Nonecomio".
    NO_SUJETO = {"hoy", "ayer", "manana", "luego", "despues", "tambien",
                 "entonces", "por", "durante", "cuando", "mientras",
                 "esta", "este", "aquel", "primero", "finalmente"}

    def corregir(m):
        sujeto, pron, raiz, term = m.group(1), m.group(2), m.group(3), m.group(4)
        if _sin_acentos(sujeto.lower()) in NO_SUJETO:
            return m.group(0)
        pron = pron or ""          # el grupo es opcional: puede venir None
        verbo = raiz + ("\u00f3" if term.lower() == "aste" else "i\u00f3")
        return "%s %s%s" % (sujeto, pron, verbo)

    return re.sub(
        r"\b(" + SUJ3 + r") (?:(te |me |nos |le |les ))?(\w+?)(aste|iste)\b",
        corregir, texto)


# ==================================================================
# CORREGIR LA PERSONA VERBAL
# ==================================================================
# El modelo copia literal las formas verbales del dictado ("al final
# lo saque") en vez de conjugarlas a segunda persona. Probado con un
# 7B y es igual o peor, asi que lo arreglamos despues de generar, de
# forma determinista: no depende de que el modelo acierte.

# Irregulares del preterito: no siguen la regla de -e/-i, hay que
# listarlos. Son los verbos mas usados al contar un dia, asi que
# cubrirlos importa mas que su numero.
IRREGULARES = {
    "fui": "fuiste", "hice": "hiciste", "tuve": "tuviste",
    "estuve": "estuviste", "pude": "pudiste", "puse": "pusiste",
    "quise": "quisiste", "supe": "supiste", "vine": "viniste",
    "dije": "dijiste", "traje": "trajiste", "conduje": "condujiste",
    "anduve": "anduviste", "di": "diste", "vi": "viste",
    "cupe": "cupiste", "produje": "produjiste",
}

# Palabras acabadas en -e/-i acentuada que NO son verbos. Sin esta
# lista, "un cafe" se convierte en "un cafaste".
NO_VERBOS = {
    # sustantivos
    "cafe", "bebe", "pie", "te", "pure", "canape", "consome", "pate",
    "buffet", "chale", "corse", "puntape", "carne", "parque", "coche",
    "frances", "ingles", "japones", "portugues", "holandes", "escoces",
    "mes", "interes", "reves", "estres", "ciempies",
    # adverbios y pronombres
    "alli", "aqui", "ahi", "asi", "casi", "si", "ya", "que", "porque",
    # nombres propios frecuentes
    "jose", "rene", "noe", "moises", "andres", "tomas", "nicolas",
    # otros
    "colibri", "maniqui", "esqui", "taxi", "bisturi", "jabali", "rubi",
    "frenesi", "alheli", "carmesi", "menu", "tabu", "champu",
}

# Presente en primera persona que tambien se cuela.
PRESENTE = {
    "estoy": "estas", "soy": "eres", "tengo": "tienes", "voy": "vas",
    "hago": "haces", "puedo": "puedes", "quiero": "quieres",
    # Futuros irregulares: pierden la vocal del infinitivo, asi que la
    # regla general ("hablare" -> "hablaras") no les vale.
    "hare": "haras", "haré": "harás",
    "tendre": "tendras", "tendré": "tendrás",
    "podre": "podras", "podré": "podrás",
    "sabre": "sabras", "sabré": "sabrás",
    "pondre": "pondras", "pondré": "pondrás",
    "vendre": "vendras", "vendré": "vendrás",
    "saldre": "saldras", "saldré": "saldrás",
    "dire": "diras", "diré": "dirás",
    "querre": "querras", "querré": "querrás",
    "siento": "sientes", "creo": "crees", "pienso": "piensas",
    "veo": "ves", "digo": "dices", "salgo": "sales", "vengo": "vienes",
    "pongo": "pones", "conozco": "conoces", "duermo": "duermes",
}


_TILDES = str.maketrans("áéíóúüÁÉÍÓÚ",
                        "aeiouuAEIOU")


def _sin_tilde(p):
    """Para comparar contra NO_VERBOS, que va escrita sin acentos."""
    return p.translate(_TILDES)


def corregir_persona(texto):
    """Pasa a segunda persona los verbos que quedaron en primera.

    Los modelos pequenos copian literal las formas verbales del dictado
    ("al final lo saque") en vez de conjugarlas. Esto lo arregla despues,
    de forma determinista, sin depender de que el modelo acierte.
    """
    t = texto

    # 1) Pronombres de primera persona -> segunda.
    #    Con IGNORECASE y conservando la mayuscula: al principio de
    #    frase viene "Me costara", y sin esto se quedaba sin convertir.
    def _pron(nuevo):
        def f(m):
            return nuevo.capitalize() if m.group(0)[0].isupper() else nuevo
        return f

    t = re.sub(r"\bme\b", _pron("te"), t, flags=re.IGNORECASE)
    t = re.sub(r"\bmis\b", _pron("tus"), t, flags=re.IGNORECASE)
    t = re.sub(r"\bmi\b", _pron("tu"), t, flags=re.IGNORECASE)
    t = re.sub(r"\bmio\b", _pron("tuyo"), t, flags=re.IGNORECASE)

    # 2) Irregulares y presentes, palabra completa, respetando mayuscula.
    def cambiar(m):
        pal = m.group(0)
        nuevo = IRREGULARES.get(pal.lower()) or PRESENTE.get(pal.lower())
        if not nuevo:
            return pal
        return nuevo.capitalize() if pal[0].isupper() else nuevo

    todos = sorted(set(IRREGULARES) | set(PRESENTE), key=len, reverse=True)
    patron = r"\b(" + "|".join(re.escape(p) for p in todos) + r")\b"
    t = re.sub(patron, cambiar, t, flags=re.IGNORECASE)

    # 3) Preterito regular. Solo con tilde, que es lo que marca la
    #    primera persona: "saque" sin tilde podria ser subjuntivo.
    #    Ojo: hay muchas palabras acabadas en -e/-i acentuada que NO son
    #    verbos ("cafe", "bebe", "alli"). Si no las excluimos, sale
    #    "te tomaste un cafaste", que es mucho peor que el fallo original.
    # Verbos cortos que la regla de longitud dejaria fuera.
    CORTOS = {"lei": "leíste", "crei": "creíste", "oi": "oíste",
              "rei": "reíste", "hui": "huiste", "fie": "fiaste"}

    def es_verbo(palabra_completa, raiz):
        # Una raiz de 1-2 letras casi nunca es un verbo conjugable
        # ("pie", "te"), salvo los pocos de la lista CORTOS.
        if palabra_completa.lower() in NO_VERBOS:
            return False
        if len(raiz) < 3:
            return palabra_completa.lower() in CORTOS
        return True

    def pret_ar(m):
        entera, raiz = m.group(0), m.group(1)
        if not es_verbo(_sin_tilde(entera), raiz):
            return entera
        # Deshacer el cambio ortografico que exige la -e:
        #   sacar   -> saque   -> sacaste    (qu -> c)
        #   llegar  -> llegue  -> llegaste   (gu -> g)
        #   empezar -> empece  -> empezaste  (c  -> z)
        if raiz.endswith("qu"):
            raiz = raiz[:-2] + "c"
        elif raiz.endswith("gu"):
            raiz = raiz[:-1]
        elif raiz.endswith("c"):
            raiz = raiz[:-1] + "z"
        return raiz + "aste"

    def pret_ir(m):
        entera, raiz = m.group(0), m.group(1)
        plano = _sin_tilde(entera).lower()
        if not es_verbo(plano, raiz):
            return entera
        # Los cortos son irregulares en la tilde: lei -> leiste.
        if plano in CORTOS:
            nuevo = CORTOS[plano]
            return nuevo.capitalize() if entera[0].isupper() else nuevo
        return raiz + "iste"

    # FUTURO primero: comparte terminacion con el preterito, y si no se
    # trata antes la regla de abajo convierte "hablare" en "hablaraste".
    # El futuro de primera persona conserva el infinitivo entero:
    #   hablar + e -> hablare -> hablaras
    #   comer  + e -> comere  -> comeras
    # La raiz acabada en -ar/-er/-ir es lo que lo distingue de un
    # preterito como "saque".
    def futuro(m):
        entera, raiz = m.group(0), m.group(1)
        if _sin_tilde(entera).lower() in NO_VERBOS:
            return entera
        nuevo = raiz + "ás"
        return nuevo.capitalize() if entera[0].isupper() else nuevo

    t = re.sub(r"\b(\w*?(?:ar|er|ir))é\b", futuro, t)

    t = re.sub(r"\b(\w+?)é\b", pret_ar, t)
    t = re.sub(r"\b(\w+?)í\b", pret_ir, t)

    return t


def recortar_a_frase(texto, limite=300):
    """Recorta a <= limite caracteres SIN partir una frase por la mitad.

    Un corte seco (texto[:300]) deja el ticket con la ultima frase
    colgando a media palabra. Aqui nos quedamos con las frases enteras
    que quepan; si ni la primera cabe, entonces si cortamos por palabra
    y cerramos con puntos suspensivos.
    """
    t = texto.strip()
    if len(t) <= limite:
        return t

    # Reconstruimos frase a frase mientras quepan enteras.
    trozos = re.split(r"(?<=[.!?])\s+", t)
    salida = ""
    for fr in trozos:
        if len(salida) + len(fr) + 1 > limite:
            break
        salida += (" " if salida else "") + fr

    if salida:
        return salida.strip()

    # Ni una frase entera cabe: cortamos por palabra, nunca a media.
    # Reservamos 3 caracteres para los puntos suspensivos, o nos
    # pasariamos del limite justo al anadirlos.
    corte = t[:limite - 3].rsplit(" ", 1)[0].rstrip(".,;:")
    return corte + "..."


def componer_hechos(hechos):
    """Arma un parrafo legible a partir de los hechos extraidos.

    En vez de pedirle al modelo que abstraiga (lo que peor hace),
    usamos lo que ya extrajo. No puede alucinar porque no genera nada.
    """
    frases = []
    for h in hechos:
        f = h.lstrip("- ").strip()
        if not f:
            continue
        f = f[0].upper() + f[1:]
        if not f.endswith((".", "!", "?")):
            f += "."
        frases.append(f)

    # Nos quedamos con las que aportan, sin pasarnos de largo para el ticket
    texto, total = [], 0
    for f in frases:
        if total + len(f) > 240:
            break
        texto.append(f); total += len(f) + 1
    return " ".join(texto) if texto else None


# ==================================================================
# HARNESS: verificar, puntuar y elegir
# ==================================================================
# Seis modelos distintos (2B a 9B) fallan la misma prueba: quien gana
# a quien, y no inventar. Mas parametros no lo arregla. Lo que si
# funciona es rodear la llamada de maquinaria:
#   1. verificar cada afirmacion por separado (pregunta binaria, que
#      es lo que un modelo pequeno SI hace bien)
#   2. generar varias candidatas y quedarse con la mejor, no con la
#      primera que pasa el filtro
#   3. descomponer la tarea cuando la directa falla
#   4. tener una salida de emergencia que no pueda alucinar

P_VERIFICAR = """Compara dos textos y di si hablan de lo mismo.

OJO: el segundo texto esta escrito en otra persona verbal. Si el
primero dice "jugue" y el segundo "jugaste", es LO MISMO: responde SI.
Lo unico que buscamos son cosas que el segundo texto se haya INVENTADO,
es decir, actividades o datos que no aparecen en el primero.

Texto 1 (lo que conto):
{texto}

Texto 2 (una frase):
{afirmacion}

El texto 2, habla de algo que aparece en el texto 1?
Responde una sola palabra: SI o NO."""


# ==================================================================
# TRANSCRIBIR EL AUDIO
# ==================================================================
def transcribir(ruta_audio):
    """Pasa un archivo de audio por whisper.cpp y devuelve el texto.

    Devuelve None si algo falla: el que llama se queda entonces con la
    transcripcion del navegador, que es peor pero siempre esta.
    """
    if not WHISPER_ACTIVO:
        print("  whisper no instalado, uso el texto del navegador")
        return None

    # whisper.cpp solo lee WAV de 16 kHz mono; el navegador manda webm
    # o mp4 segun el telefono, asi que convertimos con ffmpeg.
    wav = ruta_audio + ".wav"
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", ruta_audio,
             "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", wav],
            capture_output=True, timeout=120)
        if r.returncode != 0 or not os.path.exists(wav):
            print("  ffmpeg fallo:", r.stderr.decode("utf-8", "ignore")[-200:])
            return None

        t0 = time.time()
        r = subprocess.run(
            [WHISPER_BIN, "-m", WHISPER_MODELO, "-f", wav,
             "-l", "es",        # espanol: sin esto intenta detectar y a veces falla
             "--prompt", WHISPER_CONTEXTO,   # sesga hacia tus nombres propios
             "-np",             # sin barra de progreso
             "-nt"],            # sin marcas de tiempo, solo el texto
            capture_output=True, timeout=TIMEOUT_WHISPER)
        if r.returncode != 0:
            print("  whisper fallo:", r.stderr.decode("utf-8", "ignore")[-200:])
            return None

        texto = r.stdout.decode("utf-8", "ignore").strip()
        # whisper.cpp marca los silencios asi; fuera.
        texto = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", texto)
        texto = re.sub(r"\s+", " ", texto).strip()

        # Correcciones de nombres propios que sigue oyendo mal.
        for mal, bien in WHISPER_ARREGLOS.items():
            nuevo = re.sub(r"\b" + re.escape(mal) + r"\b", bien, texto,
                           flags=re.IGNORECASE)
            if nuevo != texto:
                print(f"  corregido: {mal} -> {bien}")
                texto = nuevo
        print(f"  whisper: {len(texto)} caracteres en {time.time()-t0:.0f}s")
        return texto or None

    except subprocess.TimeoutExpired:
        print("  whisper tardo demasiado")
        return None
    except Exception as e:
        print("  whisper reviento:", str(e)[:120])
        return None
    finally:
        for f in (wav, ruta_audio):
            try:
                os.remove(f)
            except OSError:
                pass


def soltar_modelo(modelo):
    """Saca un modelo de la RAM ahora mismo.

    Una peticion vacia con keep_alive=0 le dice a Ollama que lo
    descargue. Es lo que mantiene la Pi libre para otros proyectos:
    entre peticion y peticion, Pichon no ocupa nada.
    """
    try:
        requests.post(URL_OLLAMA, json={
            "model": modelo, "prompt": "", "stream": False,
            "keep_alive": KEEP_ALIVE_AL_TERMINAR,
        }, timeout=60)
        print(f"  [RAM] {modelo} descargado")
    except Exception as e:
        # No es critico: Ollama acabara soltandolo por su cuenta.
        print(f"  [RAM] no pude descargar {modelo}: {str(e)[:60]}")


def ram_libre_mb():
    """Para dejar constancia en el log de cuanta RAM hay en cada fase."""
    try:
        with open("/proc/meminfo") as f:
            for l in f:
                if l.startswith("MemAvailable:"):
                    return int(l.split()[1]) // 1024
    except OSError:
        pass
    return -1


P_INTENCION = """Lee lo que alguien conto de su dia y di si esta frase
habla de algo que YA HIZO o de algo que TIENE QUE HACER.

Lo que conto:
{texto}

Frase:
{frase}

Responde una sola palabra:
HECHO  - si ya paso, ya lo hizo
TAREA  - si esta pendiente, tiene que hacerlo, no debe olvidarlo"""


# Como suele escribir el 3B una tarea pendiente cuando la confunde con
# algo ya hecho. Clave = lo que escribe, valor = como deberia decirlo.
# El ORDEN importa: de mas especifico a mas general, y se aplica solo
# el primero que case. Si no, "te olvidaste de" entra por la regla de
# "olvidaste" y sale "Te tienes que de hablar".
TAREA_MAL_DICHA = [
    (r"\bte has olvidado de\b", "tienes que"),
    (r"\bte olvidaste de\b", "tienes que"),
    (r"\bse te ha olvidado\b", "tienes que"),
    (r"\bhas olvidado de\b", "tienes que"),
    (r"\bhas olvidado\b", "tienes que"),
    (r"\bolvidaste de\b", "tienes que"),
    (r"\bolvidaste\b", "tienes que"),
    (r"\bhas dejado de\b", "tienes que"),
]


def arreglar_intencion(parrafo, original):
    """Convierte en tarea pendiente lo que el modelo puso como pasado.

    Caso real: el dictado decia "no olvidar de hablar con Juan Carlos" y
    el 3B escribio "has olvidado hablar con Juan Carlos" — invierte el
    sentido y encima suena a reproche.

    El 3B no distingue tarea de hecho (4/8, y porque dice TAREA a todo).
    El 7B acierta 8/8, asi que le preguntamos a el, igual que con la
    verificacion. Solo miramos las frases sospechosas: preguntar cuesta
    ~15 s cada una.
    """
    if not VERIFICAR_ACTIVO:
        return parrafo

    sospechosas = [f for f in frases_de(parrafo)
                   if any(re.search(p, f, re.IGNORECASE)
                          for p, _ in TAREA_MAL_DICHA)]
    if not sospechosas:
        return parrafo

    for fr in sospechosas:
        r = llamar(P_INTENCION.format(texto=original, frase=fr),
                   max_tokens=5, temp=0.0, modelo=MODELO_VERIFICADOR)
        if not r or "TAREA" not in sin_tildes(r.strip().upper()):
            continue
        nueva = fr
        for patron, reemplazo in TAREA_MAL_DICHA:
            if re.search(patron, nueva, re.IGNORECASE):
                nueva = re.sub(patron, reemplazo, nueva, flags=re.IGNORECASE)
                break          # solo el primero: los patrones se solapan
        if nueva != fr:
            # "Te olvidaste de X" -> "tienes que X": la mayuscula
            # inicial se va con el reemplazo, hay que devolverla.
            if fr[:1].isupper() and nueva[:1].islower():
                nueva = nueva[0].upper() + nueva[1:]
            print(f"  intencion: era una tarea, no un hecho -> {nueva[:55]}")
            parrafo = parrafo.replace(fr, nueva)

    return parrafo


def frases_de(parrafo):
    """Parte un parrafo en afirmaciones sueltas para verificarlas."""
    partes = re.split(r"(?<=[.!?])\s+|(?:,\s+(?=luego|despues|tambien|y\s))",
                      parrafo.strip())
    return [f.strip(" ,.") for f in partes if len(f.strip()) > 12]


def verificar_parrafo(parrafo, original):
    """Comprueba frase a frase si el parrafo se lo ha inventado.

    Devuelve (buenas, malas). Preguntar "esta esto en el texto? SI/NO"
    es una tarea binaria, y ahi un 3B acierta mucho mas que redactando.
    """
    buenas, malas = [], []
    pendientes = frases_de(parrafo)
    for i, fr in enumerate(pendientes):
        # El modelo se queda en RAM entre frase y frase (recargarlo
        # costaria ~40 s cada vez), pero en la ULTIMA pedimos que se
        # descargue al responder.
        ultima = (i == len(pendientes) - 1)
        r = llamar(P_VERIFICAR.format(texto=original, afirmacion=fr),
                   max_tokens=5, temp=0.0, modelo=MODELO_VERIFICADOR,
                   keep_alive=KEEP_ALIVE_AL_TERMINAR if ultima else KEEP_ALIVE_TRABAJANDO)
        if r and sin_tildes(r.strip().upper()).startswith("SI"):
            buenas.append(fr)
        else:
            malas.append(fr)

    if pendientes:
        print(f"  [RAM] verificador liberado, libre: {ram_libre_mb()} MB")
    return buenas, malas


def puntuar(parrafo, original):
    """Nota de 0 a 100 para elegir entre varias candidatas.

    Premia cubrir lo que se conto y castiga meter cosas nuevas. Es
    deterministico: no gasta llamadas al LLM.
    """
    def utiles(s):
        return {w.strip(".,;:!?\"'") for w in sin_tildes(s.lower()).split()
                if len(w) > 4}

    orig, sal = utiles(original), utiles(parrafo)
    if not sal:
        return 0

    compartidas = len(orig & sal)
    cobertura = compartidas / max(1, len(orig))     # cuanto del dia recoge
    precision = compartidas / len(sal)              # cuanto es del dia

    nota = 100 * (0.5 * cobertura + 0.5 * precision)

    # Un parrafo de ticket quiere 120-260 caracteres: penalizamos los
    # extremos, que quedan mal en papel.
    n = len(parrafo)
    if n < 90 or n > 290:
        nota -= 15
    return max(0, nota)


def destilar_directo(limpio):
    """Via principal: generar varias candidatas y quedarse con la mejor.

    Antes nos quedabamos con la PRIMERA que pasaba el filtro. Generar
    tres y elegir por nota cuesta lo mismo en el peor caso y da mejor
    resultado, porque el 3B es muy inestable: la misma entrada produce
    una salida buena y otra desastrosa.
    """
    candidatas = []
    p = P_DIRECTO.format(texto=limpio)
    for intento in range(3):
        m = llamar(p, max_tokens=200, temp=0.25 + 0.15*intento)
        if not m:
            continue
        m = m.strip().strip('"').strip()
        if len(m) <= 25 or m.lower().startswith("parrafo"):
            continue
        if not validar_directo(m, limpio):
            print(f"  candidata {intento+1} rechazada por el validador")
            continue
        m = arreglar_concordancia(corregir_persona(m))
        nota = puntuar(m, limpio)
        candidatas.append((nota, m))
        print(f"  candidata {intento+1}: nota {nota:.0f}")
        # Una nota alta ya no va a mejorar: no gastamos mas llamadas.
        if nota >= 70:
            break

    if not candidatas:
        return None

    candidatas.sort(key=lambda c: c[0], reverse=True)
    nota, mejor = candidatas[0]

    # Verificacion final frase a frase sobre la ganadora. Si alguna se
    # la ha inventado, la quitamos en vez de tirar el parrafo entero.
    #
    # Solo merece la pena si la nota deja dudas: con nota alta ya
    # sabemos que el vocabulario viene del texto, y cada frase cuesta
    # una llamada al LLM.
    # Hay dos cosas que puede hacer el 7B: comprobar que no se ha
    # inventado nada, y distinguir tarea pendiente de hecho pasado.
    # La primera solo hace falta si la nota deja dudas; la segunda solo
    # si el parrafo tiene frases sospechosas. Si no toca ninguna, nos
    # ahorramos cargar el 7B entero.
    verificar = VERIFICAR_ACTIVO and nota < 65
    hay_sospecha = VERIFICAR_ACTIVO and any(
        re.search(p, mejor, re.IGNORECASE) for p, _ in TAREA_MAL_DICHA)

    if verificar or hay_sospecha:
        # El 3B ya no tiene nada que hacer: lo soltamos ANTES de cargar
        # el 7B, para que nunca coexistan.
        soltar_modelo(MODELO_OLLAMA)
        print(f"  [RAM] libre antes de cargar el verificador: {ram_libre_mb()} MB")

        if verificar:
            buenas, malas = verificar_parrafo(mejor, limpio)
            # Salvaguarda: si tumba casi todo, el que falla es EL
            # VERIFICADOR. Nos fiamos entonces de la nota, que es
            # deterministica.
            if len(buenas) <= len(malas) / 2:
                print(f"  el verificador rechaza {len(malas)}/{len(buenas)+len(malas)}: lo ignoro")
            elif malas:
                print("  quito por inventadas:", " / ".join(m[:40] for m in malas))
                mejor = ". ".join(b.rstrip(".") for b in buenas) + "."
                mejor = arreglar_concordancia(corregir_persona(mejor))

        if hay_sospecha:
            mejor = arreglar_intencion(mejor, limpio)
    elif VERIFICAR_ACTIVO:
        print(f"  nota {nota:.0f}: no hace falta verificar frase a frase")

    return recortar_a_frase(mejor, LARGO_TICKET)


def destilar_por_hechos(limpio):
    """Plan B: extraer hechos y redactarlos. Dos tareas faciles en vez
    de una dificil. Es lo que hace la ruta de PASADAS=2, reutilizada
    aqui como respaldo cuando la via directa no da nada aceptable."""
    hechos = extraer_hechos(limpio)
    if not hechos:
        return None
    p = P_SINTESIS.format(hechos="\n".join(hechos))
    for intento in range(2):
        m = llamar(p, max_tokens=140, temp=0.3 + 0.15*intento)
        if m and validar_sintesis(m, hechos):
            m = arreglar_concordancia(corregir_persona(m.strip().strip('"')))
            return recortar_a_frase(m, LARGO_TICKET)
    return None


def destilar_a_prueba_de_fallos(limpio):
    """Ultimo recurso: los hechos tal cual, sin redactar.

    No puede alucinar porque no genera prosa: solo ordena lo que el
    modelo copio del texto. Menos bonito que un parrafo, pero en un
    diario personal es preferible a un dia inventado.
    """
    hechos = extraer_hechos(limpio)
    if not hechos:
        return None
    return componer_hechos(hechos)


def extraer_hechos(limpio):
    """Pasada de extraccion, trozo a trozo. Es lo que mejor hace un 3B."""
    hechos = []
    for i, trozo in enumerate(trocear(limpio), 1):
        r = llamar(P_EXTRAER.format(texto=trozo), max_tokens=120, temp=0.15)
        if r:
            for l in r.split("\n"):
                l = l.strip()
                if l.startswith(("-", "*", "•")):
                    hechos.append("- " + l.lstrip("-*• ").strip())
    return hechos[:12]


def destilar(texto):
    """Convierte el dictado en el parrafo que ira al ticket.

    Con PASADAS = 1 (lo normal) el recorrido es:

        limpiar -> extraer_recordatorios -> explicitar_sujeto
          -> destilar_directo()            3 candidatas, la mejor
             |falla-> destilar_por_hechos()      extraer + redactar
             |falla-> destilar_a_prueba_de_fallos()  hechos pelados
          -> con_recordatorio()

    Con PASADAS = 2 usa la ruta antigua de dos pasadas (extraer y luego
    redactar), que era la muleta para modelos muy pequenos. Se conserva
    por si algun dia se cambia a un modelo mas flojo que el 3B.

    Pase lo que pase, al salir se sueltan los modelos de la RAM.
    """
    limpio = limpiar(texto)

    # Lo que le pides a Pichon ("recuerdame que...") no es parte del
    # relato: es una instruccion al aparato. Si se la damos al modelo
    # mezclada, escribe cosas como "Lo importante es que te imprimas
    # que tienes que echar CVs". La sacamos aparte y la volvemos a
    # pegar al final, ya formateada, sin que pase por el LLM.
    limpio, recordatorios = extraer_recordatorios(limpio)
    if recordatorios:
        print("  recordatorios aparte:", " | ".join(r[:40] for r in recordatorios))

    # Antes de que el modelo lo lea: dejar claro quien gana a quien.
    # El 3B lo invierte si el sujeto va implicito, y no hay prompt que
    # lo arregle, asi que se lo damos ya explicito.
    limpio = explicitar_sujeto(limpio)
    print(f"  limpieza: {len(texto)} -> {len(limpio)} caracteres")

    # --- una sola pasada (modelo capaz) ---
    if PASADAS == 1:
        try:
            m = destilar_directo(limpio)
            if not m:
                # La via directa no dio nada aceptable: probamos
                # descomponiendo la tarea en extraer + redactar, que es
                # lo que mejor hace un modelo pequeno. Si eso tampoco,
                # quedan los hechos pelados.
                print("  la via directa fallo, descompongo la tarea")
                m = destilar_por_hechos(limpio)
            if not m:
                print("  redactar tampoco: devuelvo los hechos sin adornar")
                m = destilar_a_prueba_de_fallos(limpio)
            return con_recordatorio(m, recordatorios)
        finally:
            # Pase lo que pase — exito, fallo o excepcion — la RAM queda
            # libre. Es lo que permite compartir la Pi con otros
            # proyectos: entre peticion y peticion, Pichon no ocupa nada.
            soltar_modelo(MODELO_OLLAMA)
            if VERIFICAR_ACTIVO:
                soltar_modelo(MODELO_VERIFICADOR)
            print(f"  [RAM] al terminar: {ram_libre_mb()} MB libres")

    # --- pasada 1: extraer, trozo a trozo ---
    hechos = []
    trozos = trocear(limpio)
    for i, trozo in enumerate(trozos, 1):
        r = llamar(P_EXTRAER.format(texto=trozo), max_tokens=120, temp=0.15)
        if r:
            for l in r.split("\n"):
                l = l.strip()
                if l.startswith(("-", "*", "•")):
                    hechos.append("- " + l.lstrip("-*• ").strip())
        print(f"  trozo {i}/{len(trozos)}: {len(hechos)} hechos acumulados")

    if not hechos:
        return None

    hechos = hechos[:12]                       # no mas de 12 lineas
    print("  hechos:", " | ".join(h[2:40] for h in hechos[:4]), "...")

    if MODO == "hechos":
        return componer_hechos(hechos)

    if MODO == "sintesis":
        # Punto medio: que REESCRIBA los hechos como parrafo. Comprimir
        # y redactar esta al alcance de un modelo pequeno; moralizar no.
        p = P_SINTESIS.format(hechos="\n".join(hechos))
        for intento in range(3):
            m = llamar(p, max_tokens=140, temp=0.3 + 0.15*intento)
            if m and validar_sintesis(m, hechos):
                return recortar_a_frase(arreglar_concordancia(corregir_persona(m.strip().strip('"'))), LARGO_TICKET)
            print(f"  sintesis pobre, reintento {intento+1}")
        print("  no salio: devuelvo los hechos en crudo")
        return componer_hechos(hechos)

    # --- pasada 2: abstraer sobre texto corto y estructurado ---
    prompt = P_MORALEJA.format(hechos="\n".join(hechos))
    m = llamar(prompt, max_tokens=120, temp=0.35)

    # Un 0.5B a veces copia el ejemplo del prompt en vez de resolver.
    # Se detecta y se reintenta con mas temperatura.
    for intento in range(2):
        if m and not copio_el_ejemplo(m):
            break
        print(f"  copio el ejemplo, reintento {intento+1}")
        m = llamar(prompt, max_tokens=120, temp=0.6 + 0.15*intento)

    if not m:
        return None
    if copio_el_ejemplo(m):
        print("  sigue copiando: devuelvo los hechos en crudo")
        return " ".join(h[2:] for h in hechos[:3])[:260]
    m = m.strip().strip('"').split("\n")[0] if m.count("\n") > 3 else m.strip().strip('"')
    return m[:260]

# ---------------- WEB (movil) ----------------
PAGINA = r"""<!doctype html><html lang="es"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Pichon</title><style>
body{font-family:system-ui,sans-serif;background:#0e0e10;color:#eee;margin:0;padding:22px}
h1{font-size:21px;margin:6px 0 14px}
textarea{width:100%;height:190px;font-size:17px;padding:12px;border-radius:14px;
 border:1px solid #333;background:#1a1a1d;color:#eee;box-sizing:border-box;line-height:1.5}
button{font-size:18px;padding:16px;border:none;border-radius:14px;color:#fff;width:100%;margin-top:10px}
.mic{background:#3a2f7a}
.mic.rec{background:#a33;animation:lat 1.4s infinite}
@keyframes lat{50%{opacity:.6}}
.ok{background:#1f7a3f}.alt{background:#2a2a2e;font-size:15px;padding:12px}
#est{margin-top:14px;color:#9c9;min-height:22px;font-size:15px}
.m{background:#16261c;border-left:3px solid #1f7a3f;padding:12px;border-radius:10px;
 margin-top:14px;font-size:16px;line-height:1.45}
.tip{color:#777;font-size:13px;margin-top:8px}
.prov{color:#888;font-style:italic}
</style></head><body>
<h1>&#128038; Cuentame el dia</h1>
<textarea id="t" placeholder="Toca Hablar y cuenta como ha ido el dia..."></textarea>
<button id="mic" class="mic" onclick="toggle()">&#127908; Hablar</button>
<button class="ok" onclick="enviar()">Guardar para manana</button>
<div class="tip">Habla por frases y haz una pausa breve entre ellas. Tambien puedes escribir, o dictar con el microfono del teclado.</div>
<div id="est"></div>
<div id="mor"></div>
<button class="alt" onclick="ver()">Ver el ticket de manana</button>
<script>
const est=document.getElementById('est'), t=document.getElementById('t');
const SR=window.SpeechRecognition||window.webkitSpeechRecognition;

// ------------------------------------------------------------------
// Chrome en Android tiene el modo continuo roto: reemite resultados y
// deja instancias solapadas, y el texto se duplica en bloques.
// Estrategia: sesiones CORTAS de una sola frase, sin provisionales.
// Cada sesion entrega UN resultado final, lo anadimos una vez, y
// relanzamos. Menos vistoso, pero no duplica.
// ------------------------------------------------------------------
let reco=null, escuchando=false, gen=0;

// ------------------------------------------------------------------
// Ademas del reconocimiento de Chrome, grabamos el audio en crudo y lo
// mandamos a la Pi, que lo pasa por Whisper. Chrome da el texto al
// momento (util para ver que se esta grabando) pero se equivoca mucho
// con habla rapida; Whisper tarda pero entiende mucho mejor.
// ------------------------------------------------------------------
let grabadora=null, trozos=[], flujo=null;

async function arrancarGrabacion(){
  // Si ya hay una grabacion en marcha (pulsaste Parar y Hablar otra
  // vez), la dejamos correr: asi el audio del dictado entero queda en
  // un solo archivo en vez de perderse el primer trozo.
  if(grabadora && grabadora.state === 'recording') return;
  try{
    flujo = await navigator.mediaDevices.getUserMedia({audio:true});
    // El tipo depende del telefono: Android suele dar webm, iOS mp4.
    // Dejamos que el navegador elija el que soporte.
    grabadora = new MediaRecorder(flujo);
    trozos = [];
    grabadora.ondataavailable = e => { if(e.data.size>0) trozos.push(e.data); };
    grabadora.start();
  }catch(err){
    // Sin grabacion seguimos igual: quedara el texto de Chrome.
    grabadora = null;
    console.log('sin grabacion de audio:', err);
  }
}

function pararGrabacion(){
  return new Promise(res => {
    if(!grabadora || grabadora.state === 'inactive'){ res(null); return; }
    grabadora.onstop = () => {
      if(flujo){ flujo.getTracks().forEach(p=>p.stop()); flujo=null; }
      res(trozos.length ? new Blob(trozos, {type: grabadora.mimeType}) : null);
    };
    try{ grabadora.stop(); }catch(e){ res(null); }
  });
}

function toggle(){ escuchando ? parar() : arrancar(); }

function arrancar(){
  if(!SR){ est.textContent='Sin soporte de voz. Usa el microfono del teclado.'; return; }
  escuchando = true;
  gen++;
  pintarBoton();
  est.textContent='escuchando...';
  arrancarGrabacion();
  lanzar(gen);
}

function lanzar(miGen){
  if(!escuchando || miGen !== gen) return;      // instancia obsoleta: fuera

  const r = new SR();
  r.lang='es-ES';
  r.continuous=false;        // una frase por sesion
  r.interimResults=false;    // solo resultados definitivos
  r.maxAlternatives=1;

  r.onresult = e => {
    if(miGen !== gen) return;                   // ignora instancias viejas
    const frase = (e.results[0] && e.results[0][0].transcript || '').trim();
    if(!frase) return;
    const actual = t.value.trim();
    t.value = actual ? actual + ' ' + frase : frase;
    t.scrollTop = t.scrollHeight;
    const n = t.value.split(/\s+/).filter(Boolean).length;
    est.textContent = 'escuchando... ('+n+' palabras)';
  };

  r.onend = () => {
    if(miGen !== gen) return;
    if(escuchando) setTimeout(()=>lanzar(miGen), 120);   // encadena
  };

  r.onerror = e => {
    if(e.error==='not-allowed'){ est.textContent='Permite el microfono'; parar(); return; }
    // no-speech / aborted / network: el onend se encarga de relanzar
  };

  reco = r;
  try{ r.start(); }catch(err){}
}

function parar(){
  escuchando = false;
  gen++;                                        // invalida lo que quede vivo
  if(reco){ try{ reco.onend=null; reco.onresult=null; reco.stop(); }catch(e){} reco=null; }
  pintarBoton();
  est.textContent = t.value.trim() ? 'Listo. Revisa el texto y guarda.' : '';
}

function pintarBoton(){
  const b=document.getElementById('mic');
  b.classList.toggle('rec', escuchando);
  b.innerHTML = escuchando ? '&#9632; Parar' : '&#127908; Hablar';
}

async function enviar(){
  if(escuchando) parar();
  const audio = await pararGrabacion();

  if(!t.value.trim() && !audio){ est.textContent='No has contado nada todavia'; return; }
  est.textContent = audio
    ? 'Transcribiendo y pensando... (un par de minutos)'
    : 'Pensando... (uno o dos minutos con textos largos)';

  const fd=new FormData();
  fd.append('texto', t.value.trim());
  if(audio){
    // La extension le dice al servidor que formato mandar a ffmpeg.
    const ext = (audio.type||'').includes('mp4') ? 'mp4' : 'webm';
    fd.append('audio', audio, 'dictado.'+ext);
  }

  fetch('/contar',{method:'POST',body:fd}).then(r=>r.json()).then(d=>{
    if(d.error){ est.textContent=d.error; return; }
    est.textContent='Guardado. Manana lo lees en papel.';
    document.getElementById('mor').innerHTML='<div class="m">'+d.moraleja+'</div>';
    // Si Whisper entendio algo distinto, lo ensenamos: asi ves lo que
    // de verdad ha quedado guardado, no lo que puso Chrome.
    if(d.texto && d.texto !== t.value.trim()){
      document.getElementById('mor').innerHTML +=
        '<div class="m" style="opacity:.6;font-size:.9em">oido: '+d.texto+'</div>';
    }
    t.value='';
  }).catch(e=>est.textContent='error: '+e);
}

function ver(){
  fetch('/previsualizar').then(r=>r.text()).then(x=>{
    document.getElementById('mor').innerHTML='<div class="m">'+x.replace(/\n/g,'<br>')+'</div>';});
}
</script></body></html>"""

@app.route("/")
def index():
    return PAGINA

@app.route("/contar", methods=["POST"])
def contar():
    texto = request.form.get("texto", "").strip()

    # El navegador manda las dos cosas: lo que entendio Chrome y el
    # audio en crudo. Si hay audio y whisper funciona, nos quedamos con
    # su transcripcion, que es bastante mejor. El texto de Chrome queda
    # de respaldo por si whisper falla.
    texto_navegador = texto
    audio = request.files.get("audio")
    if audio and WHISPER_ACTIVO:
        sufijo = os.path.splitext(audio.filename or "")[1] or ".webm"
        fd, ruta = tempfile.mkstemp(suffix=sufijo, prefix="pichon_")
        os.close(fd)
        audio.save(ruta)
        tam = os.path.getsize(ruta) / 1024
        print(f"[{datetime.now():%H:%M}] audio recibido: {tam:.0f} KB")
        mejor = transcribir(ruta)
        if mejor and len(mejor) > 20:
            texto = mejor
        else:
            print("  me quedo con la transcripcion del navegador")

    if not texto:
        return jsonify(error="texto vacio")

    moraleja = destilar(texto)
    if not moraleja:
        return jsonify(error="Ollama no responde. ¿Esta arrancado?")

    entradas = cargar()
    entradas.append({
        "ts": datetime.now().isoformat(timespec="seconds"),
        "texto": texto,
        # Guardamos tambien lo que entendio el navegador: sirve para
        # comparar las dos transcripciones cuando algo sale raro.
        "texto_navegador": texto_navegador if texto != texto_navegador else None,
        "moraleja": moraleja,
        "impresa": False,
    })
    guardar(entradas)
    print(f"[{datetime.now():%H:%M}] nueva entrada -> {moraleja[:60]}...")
    return jsonify(moraleja=moraleja, texto=texto)

def frase_del_dia():
    lista = frases()
    dias = (datetime.now().date() - datetime(2020, 1, 1).date()).days
    paso = 7
    while paso < len(lista) and _mcd(paso, len(lista)) != 1:
        paso += 1
    return lista[(dias * paso) % len(lista)]

def _mcd(a, b):
    while b: a, b = b, a % b
    return a

def moraleja_pendiente(marcar=False):
    """La moraleja mas reciente si es de las ultimas VENTANA_HORAS y no se imprimio."""
    entradas = cargar()
    limite = datetime.now() - timedelta(hours=VENTANA_HORAS)
    for e in reversed(entradas):
        if e.get("impresa"):
            continue
        if datetime.fromisoformat(e["ts"]) < limite:
            break
        if marcar:
            e["impresa"] = True
            guardar(entradas)
        return e["moraleja"]
    return None

@app.route("/ticket")
def ticket():
    """Lo que pide el ESP32 cada manana. Formato simple, sin JSON."""
    f = sin_tildes(frase_del_dia())
    m = moraleja_pendiente(marcar=True)
    salida = f"FRASE:{f}\n"
    if m:
        salida += f"MORALEJA:{sin_tildes(m)}\n"
    salida += "FIN:\n"
    print(f"[{datetime.now():%H:%M}] ticket servido al ESP32"
          f"{' (con moraleja)' if m else ' (solo frase)'}")
    return Response(salida, mimetype="text/plain")

@app.route("/previsualizar")
def previsualizar():
    f = frase_del_dia()
    m = moraleja_pendiente(marcar=False)
    return f"{f}\n\n{'— ' + m if m else '(sin moraleja: no has contado nada aun)'}"

@app.route("/historial")
def historial():
    return jsonify(cargar()[-20:])

if __name__ == "__main__":
    import sys, socket
    print("Frases:", FRASES, "|", len(frases()), "cargadas")
    print("Diario:", DIARIO)

    # La IP que tiene que apuntar el ESP32
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]
    except OSError:
        ip = "?"
    finally:
        s.close()

    # Sin argumentos -> HTTP. Hablale desde la PROPIA tablet en
    # http://localhost:5000 : el navegador permite el microfono en
    # localhost sin necesidad de certificado.
    # Con "https" -> para hablarle desde otro movil de la red.
    usar_https = len(sys.argv) > 1 and sys.argv[1] == "https"

    print()
    print("  En la tablet :  http://localhost:5000")
    print(f"  Desde el movil: {'https' if usar_https else 'http'}://{ip}:5000")
    print(f"  Para el ESP32 :  http://{ip}:5000/ticket")
    print()

    app.run(host="0.0.0.0", port=5000, threaded=True,
            ssl_context="adhoc" if usar_https else None)

# Para que arranque sola con la Pi:
#   sudo nano /etc/systemd/system/pichon.service
#   ------------------------------------------------
#   [Unit]
#   Description=Pichon diario
#   After=network-online.target
#   [Service]
#   ExecStart=/usr/bin/python3 /home/TU_USUARIO/pichon_servidor.py https
#   WorkingDirectory=/home/TU_USUARIO
#   User=TU_USUARIO
#   Restart=always
#   [Install]
#   WantedBy=multi-user.target
#   ------------------------------------------------
#   sudo systemctl enable --now pichon
