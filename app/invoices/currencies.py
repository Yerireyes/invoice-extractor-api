"""Códigos de moneda ISO 4217.

Incluye las monedas vigentes y algunas retiradas recientemente (HRK, BGN, ANG, SLL, ZWL, CUC),
porque una factura antigua puede seguir expresada en ellas. Se excluyen los códigos de metales
preciosos y de pruebas (XAU, XAG, XTS, XXX...).
"""

_CODES = """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BOV BRL BSD
    BTN BWP BYN BZD CAD CDF CHE CHF CHW CLF CLP CNY COP COU CRC CUC CUP CVE CZK DJF DKK DOP
    DZD EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HRK HTG HUF IDR ILS
    INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD LSL LYD
    MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MXV MYR MZN NAD NGN NIO NOK NPR NZD OMR
    PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK SGD SHP SLE SLL SOS
    SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH UGX USD USN UYI UYU UYW
    UZS VED VES VND VUV WST XAF XCD XCG XOF XPF YER ZAR ZMW ZWG ZWL
"""

ISO_4217_CODES: frozenset[str] = frozenset(_CODES.split())


def is_iso_4217(code: str | None) -> bool:
    """`True` si `code` es un código ISO 4217 reconocido (en mayúsculas)."""
    return code is not None and code in ISO_4217_CODES


_ZERO_DECIMAL = "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX UYI VND VUV XAF XOF XPF"

ZERO_DECIMAL_CODES: frozenset[str] = frozenset(_ZERO_DECIMAL.split())
"""Monedas sin unidades menores según ISO 4217 (los importes se redondean a la unidad)."""


def has_decimals(code: str | None) -> bool:
    """`False` para monedas sin decimales (JPY, CLP, KRW...)."""
    return code not in ZERO_DECIMAL_CODES
