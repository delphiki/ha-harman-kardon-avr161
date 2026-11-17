"""Constantes de l'intégration AVR 161."""
DOMAIN = "avr161"
CONF_MAX_VOLUME = "max_volume"                   # ancienne option (échelle interne 0..180)
CONF_MAX_VOLUME_PERCENT = "max_volume_percent"   # part de la plage de l'ampli atteinte à 100 %
DEFAULT_NAME = "Ampli"
DEFAULT_MAX_VOLUME_PERCENT = 78  # 78 % de la plage = 140/180 = -10 dB
VOLUME_STEPS = 180               # échelle interne de l'ampli : 0..180, dB = -80 + 0,5 * n


def max_volume_percent(options) -> int:
    """Volume maximal en % ; convertit l'ancienne option exprimée de 0 à 180."""
    if CONF_MAX_VOLUME_PERCENT in options:
        return int(options[CONF_MAX_VOLUME_PERCENT])
    if CONF_MAX_VOLUME in options:
        return max(1, min(100, round(options[CONF_MAX_VOLUME] * 100 / VOLUME_STEPS)))
    return DEFAULT_MAX_VOLUME_PERCENT
