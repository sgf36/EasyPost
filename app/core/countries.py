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
