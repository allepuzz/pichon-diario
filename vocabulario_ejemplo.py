# -*- coding: utf-8 -*-
"""Tus nombres propios, para que Whisper no los destroce.

Copia este archivo como vocabulario.py y pon los tuyos. vocabulario.py
esta en .gitignore: no se sube nunca, porque son los sitios y la gente
de tu vida.

Si no creas el archivo, Pichon funciona igual: solo que sin ayuda con
los nombres propios.
"""

# Prompt inicial de Whisper. Le sesga el vocabulario ANTES de
# transcribir, asi que es la via principal: cuanto mas completo, menos
# falta hace la lista de abajo.
#
# Pon tu ciudad, tu barrio, calles, nombres de gente con la que hablas,
# el nombre de tu empresa, de tu gimnasio, de tu equipo.
WHISPER_CONTEXTO = (
    "Diario personal en espanol de Espana. Pueden aparecer estos "
    "nombres: Zaragoza, calle Mayor, Ricardo, Elena, club de atletismo."
)

# Red de seguridad: lo que Whisper sigue oyendo mal aun con el prompt.
#   clave = lo que transcribe
#   valor = lo que era en realidad
# Se aplican como palabra completa, sin distinguir mayusculas.
WHISPER_ARREGLOS = {
    # "Burce": "Murcia",
    # "Sara Goza": "Zaragoza",
}
