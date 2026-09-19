/*
  PICHON — el ESP32 que imprime (v final)
  ==================================================================
  Duerme todo el dia (~10 uA). A la hora fijada despierta, le pide el
  ticket a la RASPBERRY PI y lo imprime. Si la Pi no responde, imprime
  igualmente una frase de emergencia guardada en el propio ESP32.

  Al pulsar RESET (o al enchufarlo) imprime el ticket al momento, sin
  mirar la hora. Es la forma de probarlo: no hace falta ningun boton.

  ----------------------------- CONEXIONES -----------------------------
    ESP32 GPIO17 (TX2) -> pin "TX" del header de la impresora
    ESP32 GND          -> GND del header          (masa comun)
    ESP32 GND          -> CTS del header          (control de flujo)

    Impresora con fuente propia (5-9V). NO alimentarla del ESP32:
    tira picos de 1.5-2A al imprimir y lo reiniciaria a media linea.

  El cable de datos va al pin "TX" de la impresora, NO al "RX".
  CTS a masa: le dice a la impresora "siempre lista para recibir".
  Sin esto algunas unidades se quedan esperando permiso y no imprimen.
  ======================================================================
*/
#include <WiFi.h>
#include <WiFiMulti.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <WiFiClientSecure.h>
#include <time.h>
#include <Preferences.h>
#include <esp_sleep.h>

#include "credenciales.h"

// ---------------- CONFIGURACION ----------------
// Dos redes: la de casa y el hotspot del movil. El ESP32 se engancha a
// la que encuentre, igual que hace la Pi. Asi Pichon funciona tanto si
// estas en casa como si solo hay hotspot.
//
// OJO: el ESP32 clasico SOLO ve WiFi de 2,4 GHz. Por eso la red de casa
// es la _2G_, no la _5G_: la de 5 GHz no la vera nunca.
// Las contrasenas viven en credenciales.h, que NO se sube a git.
// Copia credenciales_ejemplo.h como credenciales.h y rellenalo.
const char* RED1_SSID = RED1_SSID_VAL;
const char* RED1_PASS = RED1_PASS_VAL;
const char* RED2_SSID = RED2_SSID_VAL;
const char* RED2_PASS = RED2_PASS_VAL;

// La Pi se llama "pichon" y se anuncia por mDNS. No usamos IP fija
// porque cambia segun la red (192.168.1.x en casa, 10.50.30.x en el
// hotspot). Preguntamos por el nombre y nos da igual donde este.
const char* HOST_PI   = "pichon";        // -> pichon.local
const int   PUERTO_PI = 5000;
const char* RUTA_PI   = "/ticket";

// Si mDNS falla (algun router lo filtra), probamos estas IPs conocidas.
const char* IPS_RESPALDO[] = { IP_RESPALDO_1, IP_RESPALDO_2 };
const int   N_IPS_RESPALDO = 2;

const int HORA_IMPRESION   = 9;
const int MINUTO_IMPRESION = 0;

const int PIN_TX = 17;
const int PIN_RX = 16;   // sin usar: la impresora no nos contesta

const char* TZ_ESPANA = "CET-1CEST,M3.5.0,M10.5.0/3";

// Si la Pi no contesta, al menos sale esto
const char* FRASE_EMERGENCIA = "La Pi duerme. Tu no.";

Preferences memoria;
WiFiMulti wifiMulti;

// ============ ESC/POS ============
void escInit()           { Serial2.write(27); Serial2.write('@'); }
void escAlinear(byte a)  { Serial2.write(27); Serial2.write('a'); Serial2.write(a); }
void escTamano(byte t)   { Serial2.write(29); Serial2.write('!'); Serial2.write(t); }
void escNegrita(bool on) { Serial2.write(27); Serial2.write('E'); Serial2.write(on ? 1 : 0); }
void escAvanzar(byte n)  { Serial2.write(27); Serial2.write('d'); Serial2.write(n); }

// La impresora esta siempre alimentada por su fuente, asi que no hay
// nada que encender: solo abrir el puerto serie y resetear su estado.
void abrirImpresora() {
  Serial2.begin(9600, SERIAL_8N1, PIN_RX, PIN_TX);
  delay(300);
  escInit();
}

void cerrarImpresora() {
  Serial2.flush();
  delay(400);
}

// Corta el texto en lineas de 32 caracteres sin partir palabras
void imprimirParrafo(const String& txt) {
  String linea = "", palabra = "";
  for (unsigned int i = 0; i <= txt.length(); i++) {
    char c = (i < txt.length()) ? txt[i] : ' ';
    if (c == ' ' || c == '\n') {
      if (linea.length() + palabra.length() + 1 > 32) {
        Serial2.print(linea); Serial2.print("\n");
        linea = palabra;
      } else {
        linea += (linea.length() ? " " : "") + palabra;
      }
      palabra = "";
      if (c == '\n') { Serial2.print(linea); Serial2.print("\n"); linea = ""; }
    } else {
      palabra += c;
    }
  }
  if (linea.length()) { Serial2.print(linea); Serial2.print("\n"); }
}

// ============ RED ============
bool conectarWifi(int segundos = 25) {
  WiFi.mode(WIFI_STA);
  wifiMulti.addAP(RED1_SSID, RED1_PASS);
  wifiMulti.addAP(RED2_SSID, RED2_PASS);
  Serial.print("wifi");
  unsigned long limite = millis() + (unsigned long)segundos * 1000;
  while (millis() < limite) {
    if (wifiMulti.run() == WL_CONNECTED) {
      Serial.printf(" ok -> %s (%s)\n",
                    WiFi.SSID().c_str(), WiFi.localIP().toString().c_str());
      return true;
    }
    delay(500); Serial.print(".");
  }
  Serial.println(" FALLO");
  return false;
}

// Averigua donde esta la Pi. Primero por nombre (mDNS), y si eso falla
// prueba las IPs que sabemos que ha usado. Devuelve "" si no la encuentra.
String buscarPi() {
  if (MDNS.begin("pichon-esp32")) {
    IPAddress ip = MDNS.queryHost(HOST_PI, 4000);
    if (ip != IPAddress((uint32_t)0)) {
      Serial.printf("Pi encontrada por mDNS: %s\n", ip.toString().c_str());
      return ip.toString();
    }
  }
  Serial.println("mDNS no la encuentra, probando IPs conocidas...");
  // Aqui basta con abrir el socket TCP para saber si hay alguien en el
  // puerto 5000; no hace falta hablar TLS todavia. El handshake seguro
  // viene despues, en pedirTicket().
  for (int i = 0; i < N_IPS_RESPALDO; i++) {
    WiFiClient prueba;
    if (prueba.connect(IPS_RESPALDO[i], PUERTO_PI, 2000)) {
      prueba.stop();
      Serial.printf("Pi encontrada en %s\n", IPS_RESPALDO[i]);
      return String(IPS_RESPALDO[i]);
    }
  }
  return "";
}

// Pide el ticket. Devuelve true si la Pi contesto.
bool pedirTicket(String& frase, String& moraleja, String& proyectos) {
  String host = buscarPi();
  if (host.length() == 0) {
    Serial.println("No encuentro la Pi en la red.");
    return false;
  }
  String url = "https://" + host + ":" + String(PUERTO_PI) + RUTA_PI;
  Serial.println("GET " + url);

  // El servidor va por HTTPS porque el movil lo necesita: Chrome no da
  // acceso al microfono por HTTP salvo en localhost. El certificado es
  // autofirmado, asi que setInsecure() lo acepta sin validarlo — es lo
  // mismo que hace "curl -k". En una red local domestica no perdemos
  // nada: para interceptar esto habria que estar ya dentro del WiFi.
  WiFiClientSecure cliente;
  cliente.setInsecure();

  HTTPClient http;
  // /ticket solo lee lo ya guardado (el destilado con el LLM ocurrio
  // anoche), asi que 15 s sobran de largo.
  http.setTimeout(15000);
  http.begin(cliente, url);
  int codigo = http.GET();
  if (codigo != 200) {
    Serial.printf("La Pi no contesta (codigo %d)\n", codigo);
    http.end();
    return false;
  }
  String cuerpo = http.getString();
  http.end();

  // Formato: "FRASE:...", "MORALEJA:..." y "PROYECTOS:..." (esta
  // ultima ocupa varias lineas, hasta que llega "FIN:").
  bool enProyectos = false;
  int ini = 0;
  while (ini < (int)cuerpo.length()) {
    int fin = cuerpo.indexOf('\n', ini);
    if (fin < 0) fin = cuerpo.length();
    String l = cuerpo.substring(ini, fin);
    l.trim();

    if (l.startsWith("FRASE:"))         { frase = l.substring(6);    enProyectos = false; }
    else if (l.startsWith("MORALEJA:")) { moraleja = l.substring(9); enProyectos = false; }
    else if (l.startsWith("PROYECTOS:")) {
      proyectos = l.substring(10);
      enProyectos = true;                  // lo que siga es la lista
    }
    else if (l.startsWith("FIN:"))      { enProyectos = false; }
    else if (enProyectos && l.length())  {
      proyectos += "\n" + l;
    }
    ini = fin + 1;
  }
  return frase.length() > 0;
}

// ============ EL TICKET ============
void imprimirTicket(const String& frase, const String& moraleja,
                    const String& proyectos, const char* fecha) {
  abrirImpresora();

  escAlinear(1);
  escTamano(17); escNegrita(true);
  Serial2.print("PICHON\n");
  escTamano(0);  escNegrita(false);
  Serial2.print(fecha); Serial2.print("\n");
  Serial2.print("--------------------------------\n");

  escAlinear(0);
  imprimirParrafo(frase);

  if (moraleja.length() > 0) {
    Serial2.print("\n");
    escAlinear(1);
    escNegrita(true);
    Serial2.print("- anoche me contaste -\n");
    escNegrita(false);
    escAlinear(0);
    imprimirParrafo(moraleja);
  }

  // La lista de proyectos, si la hay. Va al final: es lo que miras de
  // reojo durante el dia, no lo que lees de un tiron al levantarte.
  if (proyectos.length() > 0) {
    Serial2.print("\n");
    escAlinear(1);
    Serial2.print("--------------------------------\n");
    escAlinear(0);
    // Ya viene con sus saltos de linea y sus guiones desde la Pi, asi
    // que se imprime tal cual: no pasa por imprimirParrafo(), que
    // juntaria las lineas.
    Serial2.print(proyectos);
    Serial2.print("\n");
  }

  escAlinear(1);
  Serial2.print("--------------------------------\n");
  escAvanzar(4);

  cerrarImpresora();
}

// ============ SUEÑO ============
uint64_t segundosHastaProxima() {
  struct tm t;
  if (!getLocalTime(&t, 500)) return 3600ULL;        // sin hora: reintenta en 1h
  int ahora    = t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec;
  int objetivo = HORA_IMPRESION * 3600 + MINUTO_IMPRESION * 60;
  int falta    = objetivo - ahora;
  if (falta <= 60) falta += 24 * 3600;                // ya paso: manana
  falta -= 180;                                        // despierta 3 min antes
  if (falta < 60) falta = 60;
  return (uint64_t)falta;
}

void aDormir() {
  uint64_t s = segundosHastaProxima();
  Serial.printf("A dormir %llu s (%.1f h)\n", s, s / 3600.0);
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  // Solo temporizador: no hay boton cableado, y habilitar ext0 sobre un
  // pin al aire lo haria despertar solo por ruido electrico.
  esp_sleep_enable_timer_wakeup(s * 1000000ULL);
  esp_deep_sleep_start();
}

// ============ CICLO ============
void hacerTicket(bool comprobarHora) {
  if (!conectarWifi()) {
    // sin wifi no hay hora ni moraleja, pero al menos avisa
    abrirImpresora();
    escAlinear(1);
    Serial2.print("SIN WIFI\n");
    escAvanzar(3);
    cerrarImpresora();
    aDormir();
  }

  configTzTime(TZ_ESPANA, "pool.ntp.org", "time.nist.gov");
  struct tm t;
  for (int i = 0; i < 30 && !getLocalTime(&t, 500); i++) delay(300);
  if (!getLocalTime(&t, 500)) aDormir();
  Serial.println(&t, "Ahora: %d/%m/%Y %H:%M:%S");

  if (comprobarHora) {
    int diaActual = t.tm_year * 1000 + t.tm_yday;
    if (memoria.getInt("ultimoDia", -1) == diaActual) {
      Serial.println("Ya imprimi hoy.");
      aDormir();
    }
    // desperto 3 min antes: espera a la hora exacta
    int objetivo = HORA_IMPRESION * 60 + MINUTO_IMPRESION;
    int ahoraMin = t.tm_hour * 60 + t.tm_min;
    while (ahoraMin < objetivo && (objetivo - ahoraMin) <= 5) {
      delay(10000);
      if (getLocalTime(&t, 500)) ahoraMin = t.tm_hour * 60 + t.tm_min;
    }
    if (ahoraMin < objetivo || ahoraMin > objetivo + 10) {
      Serial.println("Fuera de la ventana.");
      aDormir();
    }
    memoria.putInt("ultimoDia", diaActual);
  }

  String frase = "", moraleja = "", proyectos = "";
  if (!pedirTicket(frase, moraleja, proyectos)) frase = FRASE_EMERGENCIA;

  char fecha[32];
  strftime(fecha, sizeof(fecha), "%d/%m/%Y  %H:%M", &t);
  imprimirTicket(frase, moraleja, proyectos, fecha);
  Serial.println(">> Ticket impreso.");

  aDormir();
}

void setup() {
  Serial.begin(115200);
  delay(200);
  memoria.begin("pichon", false);

  esp_sleep_wakeup_cause_t causa = esp_sleep_get_wakeup_cause();
  Serial.printf("Despertado por: %d\n", causa);

  // Si la causa NO es el temporizador, es que alguien pulso RESET o lo
  // acaba de enchufar. Eso es una peticion humana: imprime ya, sin
  // mirar la hora. Es como se prueba el aparato sin esperar a las 9:00.
  if (causa != ESP_SLEEP_WAKEUP_TIMER) {
    Serial.println("Arranque manual (RESET): imprimo ya.");
    hacerTicket(false);
  } else {
    hacerTicket(true);                // temporizador: ciclo normal
  }
}

void loop() { }   // nunca llega: siempre acaba en deep sleep
