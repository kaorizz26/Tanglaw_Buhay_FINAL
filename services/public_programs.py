"""Public-facing program metadata used by the information pages."""

VERIFIED_STATISTIC = "verified"
PENDING_VERIFICATION = "pending_verification"
RESEARCH_ESTIMATE = "research_estimate"

FOUR_PS_COVERAGE_REPORT_URL = (
    "https://pantawid.dswd.gov.ph/wp-content/uploads/2023/06/"
    "4Ps-Fourth-Quarter-Report-on-Program-Coverage.pdf"
)

PUBLIC_PROGRAMS = {
    "4ps": {
        "code": "4Ps",
        "name": "Pantawid Pamilyang Pilipino Program",
        "icon": "assets/icons/4ps_icon.png",
        "card_description": "Suporta para sa kalusugan, nutrisyon, at edukasyon ng mga kwalipikadong pamilya.",
        "summary": "Alamin ang pangunahing layunin ng 4Ps at ang mga hakbang bago humingi ng opisyal na assessment.",
        "purpose": "Ang 4Ps ay isang pambansang programang tumutulong sa mga kwalipikadong pamilyang nangangailangan, na may pagtuon sa kalusugan, nutrisyon, at edukasyon ng mga anak. Ang pagiging kwalipikado ay sinusuri ng mga awtorisadong tanggapan batay sa umiiral na patakaran.",
        "statistic": {
            "publication_status": RESEARCH_ESTIMATE,
            "value": 1833,
            "unit": "households",
            "priority_breakdown": (
                {"key": "primary", "percentage": 60, "value": 1100},
                {"key": "secondary", "percentage": 35, "value": 641},
                {"key": "no_priority", "percentage": 5, "value": 92},
            ),
            "source_kind": "lgu_provided_cbms",
            "reporting_period": None,
            "cutoff_date": None,
            "approved_for_public_display": True,
            "official_reference": {
                "value": 1833,
                "reporting_date": "2022-12-31",
                "source_url": FOUR_PS_COVERAGE_REPORT_URL,
            },
        },
    },
    "spisc": {
        "code": "SPISC",
        "name": "Social Pension for Indigent Senior Citizens",
        "icon": "assets/icons/spisc_icon.png",
        "card_description": "Tulong pinansyal para sa mga kwalipikadong nakatatandang mamamayang nangangailangan.",
        "summary": "Alamin ang pangkalahatang impormasyon tungkol sa suportang inilaan para sa mga kwalipikadong indigent senior citizen.",
        "purpose": "Ang SPISC ay nagbibigay ng suportang panlipunan sa mga kwalipikadong nakatatandang mamamayan na kabilang sa sektor na nangangailangan. Ang opisyal na assessment at beripikasyon ng talaan ay isinasagawa ng awtorisadong tanggapan.",
        "statistic": {
            "publication_status": RESEARCH_ESTIMATE,
            "value": 2450,
            "unit": "individuals",
            "priority_breakdown": (
                {"key": "primary", "percentage": 60, "value": 1470},
                {"key": "secondary", "percentage": 35, "value": 858},
                {"key": "no_priority", "percentage": 5, "value": 122},
            ),
            "source_kind": "lgu_provided_cbms",
            "reporting_period": None,
            "cutoff_date": None,
            "approved_for_public_display": True,
        },
    },
    "slp": {
        "code": "SLP",
        "name": "Sustainable Livelihood Program",
        "icon": "assets/icons/slp_icon.png",
        "card_description": "Tulong sa kabuhayan sa pamamagitan ng pagsasanay at pagbuo ng mapagkakakitaang gawain.",
        "summary": "Alamin kung paano sinusuportahan ng SLP ang pagpapaunlad ng kakayahan at kabuhayan.",
        "purpose": "Ang SLP ay tumutulong sa pagpapaunlad ng kasanayan at mga oportunidad sa kabuhayan sa pamamagitan ng angkop na paghahanda, pagsasanay, at suporta. Ang uri ng tulong ay nakabatay sa assessment at kasalukuyang programa ng tanggapan.",
        "statistic": {
            "publication_status": RESEARCH_ESTIMATE,
            "value": 176,
            "unit": "households",
            "priority_breakdown": (
                {"key": "primary", "percentage": 60, "value": 106},
                {"key": "secondary", "percentage": 35, "value": 61},
                {"key": "no_priority", "percentage": 5, "value": 9},
            ),
            "source_kind": "lgu_provided_cbms",
            "reporting_period": None,
            "cutoff_date": None,
            "approved_for_public_display": True,
        },
    },
    "tupad": {
        "code": "TUPAD",
        "name": "Tulong Panghanapbuhay sa Ating Disadvantaged/Displaced Workers",
        "icon": "assets/icons/tupad_icon.png",
        "card_description": "Pansamantalang hanapbuhay para sa mga manggagawang kapos, nawalan, o nawalan ng pagkakataong magtrabaho.",
        "summary": "Alamin ang layunin ng pansamantalang programang pangkabuhayan para sa mga manggagawang nangangailangan.",
        "purpose": "Ang TUPAD ay isang community-based na pansamantalang employment program para sa mga kwalipikadong manggagawang kapos, nawalan, o seasonal ang trabaho. Ang pagpili at mga susunod na hakbang ay pinangangasiwaan ng awtorisadong tanggapan.",
        "statistic": {
            "publication_status": RESEARCH_ESTIMATE,
            "value": 1258,
            "unit": "individuals",
            "priority_breakdown": (
                {"key": "primary", "percentage": 60, "value": 755},
                {"key": "secondary", "percentage": 35, "value": 440},
                {"key": "no_priority", "percentage": 5, "value": 63},
            ),
            "source_kind": "lgu_provided_cbms",
            "reporting_period": None,
            "cutoff_date": None,
            "approved_for_public_display": True,
        },
    },
}
