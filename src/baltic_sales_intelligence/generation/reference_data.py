"""Frozen public reference catalogues from SPEC-03."""

from __future__ import annotations

COUNTRIES = (
    ("LT", "Lithuania", "EUR"),
    ("LV", "Latvia", "EUR"),
    ("EE", "Estonia", "EUR"),
)

REGIONS = (
    ("LT-VL", "LT", "Vilnius County", "LT_COUNTY_NUTS3_2018"),
    ("LT-KU", "LT", "Kaunas County", "LT_COUNTY_NUTS3_2018"),
    ("LT-KL", "LT", "Klaipeda County", "LT_COUNTY_NUTS3_2018"),
    ("LT-SA", "LT", "Siauliai County", "LT_COUNTY_NUTS3_2018"),
    ("LT-PN", "LT", "Panevezys County", "LT_COUNTY_NUTS3_2018"),
    ("LV-RI", "LV", "Riga Statistical Region", "LV_STAT_REGION_2024"),
    ("LV-LG", "LV", "Latgale Statistical Region", "LV_STAT_REGION_2024"),
    ("LV-KU", "LV", "Kurzeme Statistical Region", "LV_STAT_REGION_2024"),
    ("LV-ZE", "LV", "Zemgale Statistical Region", "LV_STAT_REGION_2024"),
    ("EE-37", "EE", "Harju County", "EE_COUNTY_CURRENT"),
    ("EE-79", "EE", "Tartu County", "EE_COUNTY_CURRENT"),
    ("EE-45", "EE", "Ida-Viru County", "EE_COUNTY_CURRENT"),
    ("EE-67", "EE", "Parnu County", "EE_COUNTY_CURRENT"),
)

CITIES = (
    ("LT-VILNIUS", "LT-VL", "LT", "Vilnius"),
    ("LT-KAUNAS", "LT-KU", "LT", "Kaunas"),
    ("LT-KLAIPEDA", "LT-KL", "LT", "Klaipeda"),
    ("LT-SIAULIAI", "LT-SA", "LT", "Siauliai"),
    ("LT-PANEVEZYS", "LT-PN", "LT", "Panevezys"),
    ("LV-RIGA", "LV-RI", "LV", "Riga"),
    ("LV-DAUGAVPILS", "LV-LG", "LV", "Daugavpils"),
    ("LV-LIEPAJA", "LV-KU", "LV", "Liepaja"),
    ("LV-JELGAVA", "LV-ZE", "LV", "Jelgava"),
    ("LV-VENTSPILS", "LV-KU", "LV", "Ventspils"),
    ("EE-TALLINN", "EE-37", "EE", "Tallinn"),
    ("EE-TARTU", "EE-79", "EE", "Tartu"),
    ("EE-NARVA", "EE-45", "EE", "Narva"),
    ("EE-PARNU", "EE-67", "EE", "Parnu"),
)

SEGMENTS = (
    ("SMALL_BUSINESS", "Small Business"),
    ("MID_MARKET", "Mid-Market"),
    ("ENTERPRISE", "Enterprise"),
    ("PUBLIC_SECTOR", "Public Sector"),
    ("TRADE_RESELLER", "Trade / Reseller"),
)

INDUSTRIES = (
    ("MANUFACTURING", "Manufacturing"),
    ("CONSTRUCTION", "Construction"),
    ("LOGISTICS", "Logistics"),
    ("AUTOMOTIVE", "Automotive"),
    ("FACILITIES_MANAGEMENT", "Facilities Management"),
    ("AGRICULTURE", "Agriculture"),
    ("FOOD_PROCESSING", "Food Processing"),
    ("RETAIL", "Retail"),
    ("ENGINEERING", "Engineering"),
    ("UTILITIES", "Utilities"),
    ("PUBLIC_SECTOR", "Public Sector"),
)

CHANNELS = (
    ("ACCOUNT_MANAGER", "Account Manager"),
    ("INSIDE_SALES", "Inside Sales"),
    ("WEB", "Web"),
    ("EMAIL_PHONE", "Email / Phone Order"),
    ("TRADE_COUNTER", "Trade Counter"),
)

COMPANY_SIZE_BANDS = ("micro", "small", "medium", "large")
ACCOUNT_STATUSES = ("active", "suspended", "closed")
CREDIT_TIERS = ("tier_1", "tier_2", "tier_3")

PUBLIC_COLUMNS = {
    "countries": ("country_code", "country_name", "reporting_currency"),
    "regions": ("region_id", "country_code", "region_name", "region_scheme"),
    "cities": ("city_id", "region_id", "country_code", "city_name"),
    "customer_segments": ("segment_code", "segment_name"),
    "industries": ("industry_code", "industry_name"),
    "product_categories": ("category_id", "category_name"),
    "product_subcategories": ("subcategory_id", "category_id", "subcategory_name"),
    "suppliers": ("supplier_id", "supplier_name", "supplier_country_code"),
    "sales_channels": ("channel_code", "channel_name"),
    "customers": (
        "customer_id",
        "customer_name",
        "acquisition_date",
        "account_status",
        "industry_code",
        "credit_tier",
        "preferred_channel_code",
    ),
    "customer_attribute_history": (
        "customer_version_id",
        "customer_id",
        "valid_from",
        "valid_to",
        "segment_code",
        "company_size_band",
        "home_city_id",
        "home_region_id",
        "home_country_code",
        "is_current",
    ),
    "salespeople": (
        "salesperson_id",
        "salesperson_name",
        "primary_country_code",
        "primary_region_id",
        "hire_date",
        "termination_date",
        "manager_salesperson_id",
    ),
    "customer_account_assignments": (
        "assignment_id",
        "customer_id",
        "salesperson_id",
        "valid_from",
        "valid_to",
    ),
    "products": (
        "product_id",
        "sku",
        "product_name",
        "subcategory_id",
        "launch_date",
        "discontinued_date",
        "lifecycle_status",
        "reference_list_unit_price_eur",
        "reference_standard_unit_cost_eur",
    ),
    "product_supplier_relationships": (
        "product_supplier_relationship_id",
        "product_id",
        "supplier_id",
        "relationship_type",
        "valid_from",
        "valid_to",
    ),
    "date_dimension": (
        "date_key",
        "full_date",
        "calendar_year",
        "calendar_quarter",
        "calendar_month",
        "month_start_date",
        "iso_week",
        "iso_week_year",
        "iso_weekday",
        "day_of_month",
        "day_of_year",
        "is_weekend",
        "is_within_reporting_window",
    ),
}

PRIVATE_LABEL_NAMES = frozenset(
    {"story_id", "latent_churn_state", "defect_id", "scenario_id", "risk_tier"}
)
