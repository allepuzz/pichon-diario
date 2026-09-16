/*
  Copia este archivo como "credenciales.h" y pon tus datos.
  credenciales.h esta en .gitignore: no se sube nunca.

  OJO: el ESP32 clasico SOLO ve WiFi de 2,4 GHz. Si tu router emite en
  las dos bandas con nombres distintos, usa la de 2,4 GHz (suele ser la
  que NO lleva "5G" en el nombre).
*/
#ifndef CREDENCIALES_H
#define CREDENCIALES_H

// Red principal: la de casa
#define RED1_SSID_VAL "TU_WIFI_DE_CASA_2.4GHz"
#define RED1_PASS_VAL "tu_contrasena"

// Red secundaria: el hotspot del movil, para cuando no estas en casa
#define RED2_SSID_VAL "TU_HOTSPOT"
#define RED2_PASS_VAL "tu_contrasena_hotspot"

// IPs de respaldo por si mDNS falla. Se averiguan con: hostname -I
#define IP_RESPALDO_1 "192.168.1.100"
#define IP_RESPALDO_2 "192.168.4.100"

#endif
