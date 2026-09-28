"""Country list + per-country field-label conventions for the address form.

Returns "kind" identifiers rather than display text, since the UI layer
translates them via app.i18n.tr() — see _STATE_LABEL_KEYS/_POSTAL_LABEL_KEYS
in app/ui/views/address_book_view.py. State-label overrides are curated for
the countries EasyPost ships to/from most often; everywhere else falls back
to a generic "default" kind rather than guessing at unfamiliar
administrative-division terminology.
"""

DEFAULT_STATE_LABEL_KIND = "default"
DEFAULT_POSTAL_LABEL_KIND = "postal"

STATE_LABEL_KIND_OVERRIDES = {
    "US": "state",
    "CA": "province",
    "GB": "county",
    "IE": "county",
    "AU": "state_territory",
    "JP": "prefecture",
    "CN": "province",
    "DE": "state",
    "FR": "region",
    "IT": "province",
    "ES": "province",
    "MX": "state",
    "BR": "state",
    "IN": "state",
    "NL": "province",
    "CH": "canton",
    "RU": "region",
    "NZ": "region",
    "ZA": "province",
    "AE": "emirate",
}

POSTAL_LABEL_KIND_OVERRIDES = {
    "US": "zip",
}


def state_label_kind_for(country_code: str) -> str:
    return STATE_LABEL_KIND_OVERRIDES.get((country_code or "").upper(), DEFAULT_STATE_LABEL_KIND)


def postal_label_kind_for(country_code: str) -> str:
    return POSTAL_LABEL_KIND_OVERRIDES.get((country_code or "").upper(), DEFAULT_POSTAL_LABEL_KIND)


# (ISO 3166-1 alpha-2 code, English short name) — sorted by name at import time.
_RAW_COUNTRIES = [
    ("AF", "Afghanistan"), ("AL", "Albania"), ("DZ", "Algeria"), ("AD", "Andorra"),
    ("AO", "Angola"), ("AG", "Antigua and Barbuda"), ("AR", "Argentina"), ("AM", "Armenia"),
    ("AU", "Australia"), ("AT", "Austria"), ("AZ", "Azerbaijan"), ("BS", "Bahamas"),
    ("BH", "Bahrain"), ("BD", "Bangladesh"), ("BB", "Barbados"), ("BY", "Belarus"),
    ("BE", "Belgium"), ("BZ", "Belize"), ("BJ", "Benin"), ("BT", "Bhutan"),
    ("BO", "Bolivia"), ("BA", "Bosnia and Herzegovina"), ("BW", "Botswana"), ("BR", "Brazil"),
    ("BN", "Brunei"), ("BG", "Bulgaria"), ("BF", "Burkina Faso"), ("BI", "Burundi"),
    ("KH", "Cambodia"), ("CM", "Cameroon"), ("CA", "Canada"), ("CV", "Cape Verde"),
    ("CF", "Central African Republic"), ("TD", "Chad"), ("CL", "Chile"), ("CN", "China"),
    ("CO", "Colombia"), ("KM", "Comoros"), ("CG", "Congo"), ("CD", "Congo (DRC)"),
    ("CR", "Costa Rica"), ("HR", "Croatia"), ("CU", "Cuba"), ("CY", "Cyprus"),
    ("CZ", "Czech Republic"), ("DK", "Denmark"), ("DJ", "Djibouti"), ("DM", "Dominica"),
    ("DO", "Dominican Republic"), ("EC", "Ecuador"), ("EG", "Egypt"), ("SV", "El Salvador"),
    ("GQ", "Equatorial Guinea"), ("ER", "Eritrea"), ("EE", "Estonia"), ("SZ", "Eswatini"),
    ("ET", "Ethiopia"), ("FJ", "Fiji"), ("FI", "Finland"), ("FR", "France"),
    ("GA", "Gabon"), ("GM", "Gambia"), ("GE", "Georgia"), ("DE", "Germany"),
    ("GH", "Ghana"), ("GR", "Greece"), ("GD", "Grenada"), ("GT", "Guatemala"),
    ("GN", "Guinea"), ("GW", "Guinea-Bissau"), ("GY", "Guyana"), ("HT", "Haiti"),
    ("HN", "Honduras"), ("HK", "Hong Kong"), ("HU", "Hungary"), ("IS", "Iceland"),
    ("IN", "India"), ("ID", "Indonesia"), ("IR", "Iran"), ("IQ", "Iraq"),
    ("IE", "Ireland"), ("IL", "Israel"), ("IT", "Italy"), ("JM", "Jamaica"),
    ("JP", "Japan"), ("JO", "Jordan"), ("KZ", "Kazakhstan"), ("KE", "Kenya"),
    ("KI", "Kiribati"), ("KW", "Kuwait"), ("KG", "Kyrgyzstan"), ("LA", "Laos"),
    ("LV", "Latvia"), ("LB", "Lebanon"), ("LS", "Lesotho"), ("LR", "Liberia"),
    ("LY", "Libya"), ("LI", "Liechtenstein"), ("LT", "Lithuania"), ("LU", "Luxembourg"),
    ("MO", "Macau"), ("MG", "Madagascar"), ("MW", "Malawi"), ("MY", "Malaysia"),
    ("MV", "Maldives"), ("ML", "Mali"), ("MT", "Malta"), ("MH", "Marshall Islands"),
    ("MR", "Mauritania"), ("MU", "Mauritius"), ("MX", "Mexico"), ("FM", "Micronesia"),
    ("MD", "Moldova"), ("MC", "Monaco"), ("MN", "Mongolia"), ("ME", "Montenegro"),
    ("MA", "Morocco"), ("MZ", "Mozambique"), ("MM", "Myanmar"), ("NA", "Namibia"),
    ("NR", "Nauru"), ("NP", "Nepal"), ("NL", "Netherlands"), ("NZ", "New Zealand"),
    ("NI", "Nicaragua"), ("NE", "Niger"), ("NG", "Nigeria"), ("KP", "North Korea"),
    ("MK", "North Macedonia"), ("NO", "Norway"), ("OM", "Oman"), ("PK", "Pakistan"),
    ("PW", "Palau"), ("PA", "Panama"), ("PG", "Papua New Guinea"), ("PY", "Paraguay"),
    ("PE", "Peru"), ("PH", "Philippines"), ("PL", "Poland"), ("PT", "Portugal"),
    ("PR", "Puerto Rico"), ("QA", "Qatar"), ("RO", "Romania"), ("RU", "Russia"),
    ("RW", "Rwanda"), ("KN", "Saint Kitts and Nevis"), ("LC", "Saint Lucia"),
    ("VC", "Saint Vincent and the Grenadines"), ("WS", "Samoa"), ("SM", "San Marino"),
    ("ST", "Sao Tome and Principe"), ("SA", "Saudi Arabia"), ("SN", "Senegal"),
    ("RS", "Serbia"), ("SC", "Seychelles"), ("SL", "Sierra Leone"), ("SG", "Singapore"),
    ("SK", "Slovakia"), ("SI", "Slovenia"), ("SB", "Solomon Islands"), ("SO", "Somalia"),
    ("ZA", "South Africa"), ("KR", "South Korea"), ("SS", "South Sudan"), ("ES", "Spain"),
    ("LK", "Sri Lanka"), ("SD", "Sudan"), ("SR", "Suriname"), ("SE", "Sweden"),
    ("CH", "Switzerland"), ("SY", "Syria"), ("TW", "Taiwan"), ("TJ", "Tajikistan"),
    ("TZ", "Tanzania"), ("TH", "Thailand"), ("TL", "Timor-Leste"), ("TG", "Togo"),
    ("TO", "Tonga"), ("TT", "Trinidad and Tobago"), ("TN", "Tunisia"), ("TR", "Turkey"),
    ("TM", "Turkmenistan"), ("TV", "Tuvalu"), ("UG", "Uganda"), ("UA", "Ukraine"),
    ("AE", "United Arab Emirates"), ("GB", "United Kingdom"), ("US", "United States"),
    ("UY", "Uruguay"), ("UZ", "Uzbekistan"), ("VU", "Vanuatu"), ("VA", "Vatican City"),
    ("VE", "Venezuela"), ("VN", "Vietnam"), ("YE", "Yemen"), ("ZM", "Zambia"),
    ("ZW", "Zimbabwe"),
]

COUNTRIES = sorted(_RAW_COUNTRIES, key=lambda pair: pair[1])

_ALPHA2_TO_ALPHA3 = {
    "AF": "AFG", "AL": "ALB", "DZ": "DZA", "AD": "AND", "AO": "AGO",
    "AG": "ATG", "AR": "ARG", "AM": "ARM", "AU": "AUS", "AT": "AUT",
    "AZ": "AZE", "BS": "BHS", "BH": "BHR", "BD": "BGD", "BB": "BRB",
    "BY": "BLR", "BE": "BEL", "BZ": "BLZ", "BJ": "BEN", "BT": "BTN",
    "BO": "BOL", "BA": "BIH", "BW": "BWA", "BR": "BRA", "BN": "BRN",
    "BG": "BGR", "BF": "BFA", "BI": "BDI", "KH": "KHM", "CM": "CMR",
    "CA": "CAN", "CV": "CPV", "CF": "CAF", "TD": "TCD", "CL": "CHL",
    "CN": "CHN", "CO": "COL", "KM": "COM", "CG": "COG", "CD": "COD",
    "CR": "CRI", "HR": "HRV", "CU": "CUB", "CY": "CYP", "CZ": "CZE",
    "DK": "DNK", "DJ": "DJI", "DM": "DMA", "DO": "DOM", "EC": "ECU",
    "EG": "EGY", "SV": "SLV", "GQ": "GNQ", "ER": "ERI", "EE": "EST",
    "SZ": "SWZ", "ET": "ETH", "FJ": "FJI", "FI": "FIN", "FR": "FRA",
    "GA": "GAB", "GM": "GMB", "GE": "GEO", "DE": "DEU", "GH": "GHA",
    "GR": "GRC", "GD": "GRD", "GT": "GTM", "GN": "GIN", "GW": "GNB",
    "GY": "GUY", "HT": "HTI", "HN": "HND", "HK": "HKG", "HU": "HUN",
    "IS": "ISL", "IN": "IND", "ID": "IDN", "IR": "IRN", "IQ": "IRQ",
    "IE": "IRL", "IL": "ISR", "IT": "ITA", "JM": "JAM", "JP": "JPN",
    "JO": "JOR", "KZ": "KAZ", "KE": "KEN", "KI": "KIR", "KW": "KWT",
    "KG": "KGZ", "LA": "LAO", "LV": "LVA", "LB": "LBN", "LS": "LSO",
    "LR": "LBR", "LY": "LBY", "LI": "LIE", "LT": "LTU", "LU": "LUX",
    "MO": "MAC", "MG": "MDG", "MW": "MWI", "MY": "MYS", "MV": "MDV",
    "ML": "MLI", "MT": "MLT", "MH": "MHL", "MR": "MRT", "MU": "MUS",
    "MX": "MEX", "FM": "FSM", "MD": "MDA", "MC": "MCO", "MN": "MNG",
    "ME": "MNE", "MA": "MAR", "MZ": "MOZ", "MM": "MMR", "NA": "NAM",
    "NR": "NRU", "NP": "NPL", "NL": "NLD", "NZ": "NZL", "NI": "NIC",
    "NE": "NER", "NG": "NGA", "KP": "PRK", "MK": "MKD", "NO": "NOR",
    "OM": "OMN", "PK": "PAK", "PW": "PLW", "PA": "PAN", "PG": "PNG",
    "PY": "PRY", "PE": "PER", "PH": "PHL", "PL": "POL", "PT": "PRT",
    "PR": "PRI", "QA": "QAT", "RO": "ROU", "RU": "RUS", "RW": "RWA",
    "KN": "KNA", "LC": "LCA", "VC": "VCT", "WS": "WSM", "SM": "SMR",
    "ST": "STP", "SA": "SAU", "SN": "SEN", "RS": "SRB", "SC": "SYC",
    "SL": "SLE", "SG": "SGP", "SK": "SVK", "SI": "SVN", "SB": "SLB",
    "SO": "SOM", "ZA": "ZAF", "KR": "KOR", "SS": "SSD", "ES": "ESP",
    "LK": "LKA", "SD": "SDN", "SR": "SUR", "SE": "SWE", "CH": "CHE",
    "SY": "SYR", "TW": "TWN", "TJ": "TJK", "TZ": "TZA", "TH": "THA",
    "TL": "TLS", "TG": "TGO", "TO": "TON", "TT": "TTO", "TN": "TUN",
    "TR": "TUR", "TM": "TKM", "TV": "TUV", "UG": "UGA", "UA": "UKR",
    "AE": "ARE", "GB": "GBR", "US": "USA", "UY": "URY", "UZ": "UZB",
    "VU": "VUT", "VA": "VAT", "VE": "VEN", "VN": "VNM", "YE": "YEM",
    "ZM": "ZMB", "ZW": "ZWE",
}


def to_alpha3(alpha2: str) -> str:
    """Convert an ISO 3166-1 alpha-2 code to alpha-3.

    Returns the input unchanged if it is already three characters or
    if no mapping is found.
    """
    code = (alpha2 or "").upper().strip()
    if len(code) == 3:
        return code
    return _ALPHA2_TO_ALPHA3.get(code, code)
