"""Wildfire hotspots worldwide, each with a major historic fire for replay.

Any point on land can be analysed; these are the presets shown on the map
and used for validation.
"""

REGIONS = {
    # Europe
    "pedrogao": {"name": "Pedrógão Grande", "country": "Portugal", "continent": "Europe",
                 "lat": 39.92, "lon": -8.15, "fire": "2017-06-17"},
    "monchique": {"name": "Monchique, Algarve", "country": "Portugal", "continent": "Europe",
                  "lat": 37.32, "lon": -8.55, "fire": "2018-08-03"},
    "madeira": {"name": "Madeira", "country": "Portugal", "continent": "Europe",
                "lat": 32.75, "lon": -17.00, "fire": "2024-08-14"},
    "tenerife": {"name": "Tenerife", "country": "Spain", "continent": "Europe",
                 "lat": 28.40, "lon": -16.45, "fire": "2023-08-15"},
    "culebra": {"name": "Sierra de la Culebra", "country": "Spain", "continent": "Europe",
                "lat": 41.90, "lon": -6.30, "fire": "2022-06-15"},
    "landiras": {"name": "Landiras, Gironde", "country": "France", "continent": "Europe",
                 "lat": 44.55, "lon": -0.42, "fire": "2022-07-12"},
    "montiferru": {"name": "Montiferru, Sardinia", "country": "Italy", "continent": "Europe",
                   "lat": 40.10, "lon": 8.60, "fire": "2021-07-24"},
    "palermo": {"name": "Palermo, Sicily", "country": "Italy", "continent": "Europe",
                "lat": 38.10, "lon": 13.30, "fire": "2023-07-24"},
    "mati": {"name": "Mati, East Attica", "country": "Greece", "continent": "Europe",
             "lat": 38.04, "lon": 24.00, "fire": "2018-07-23"},
    "north-evia": {"name": "Mantoudi, North Evia", "country": "Greece", "continent": "Europe",
                   "lat": 38.80, "lon": 23.48, "fire": "2021-08-03"},
    "rhodes": {"name": "South Rhodes", "country": "Greece", "continent": "Europe",
               "lat": 36.10, "lon": 27.85, "fire": "2023-07-18"},
    "dadia": {"name": "Dadia Forest, Evros", "country": "Greece", "continent": "Europe",
              "lat": 41.12, "lon": 26.20, "fire": "2023-08-19"},
    "varnavas": {"name": "Varnavas, NE Attica", "country": "Greece", "continent": "Europe",
                 "lat": 38.22, "lon": 23.93, "fire": "2024-08-11"},
    "arakapas": {"name": "Arakapas, Limassol", "country": "Cyprus", "continent": "Europe",
                 "lat": 34.85, "lon": 33.15, "fire": "2021-07-03"},
    # North America
    "paradise": {"name": "Paradise (Camp Fire), California", "country": "United States",
                 "continent": "North America", "lat": 39.76, "lon": -121.62, "fire": "2018-11-08"},
    "palisades": {"name": "Pacific Palisades, Los Angeles", "country": "United States",
                  "continent": "North America", "lat": 34.07, "lon": -118.54, "fire": "2025-01-07"},
    "lahaina": {"name": "Lahaina, Maui", "country": "United States",
                "continent": "North America", "lat": 20.88, "lon": -156.68, "fire": "2023-08-08"},
    "fort-mcmurray": {"name": "Fort McMurray, Alberta", "country": "Canada",
                      "continent": "North America", "lat": 56.73, "lon": -111.38, "fire": "2016-05-01"},
    "jasper": {"name": "Jasper, Alberta", "country": "Canada",
               "continent": "North America", "lat": 52.87, "lon": -118.08, "fire": "2024-07-22"},
    # South America
    "vina-del-mar": {"name": "Viña del Mar, Valparaíso", "country": "Chile",
                     "continent": "South America", "lat": -33.05, "lon": -71.50, "fire": "2024-02-02"},
    # Africa
    "knysna": {"name": "Knysna, Western Cape", "country": "South Africa",
               "continent": "Africa", "lat": -34.04, "lon": 23.05, "fire": "2017-06-07"},
    # Asia
    "manavgat": {"name": "Manavgat, Antalya", "country": "Türkiye", "continent": "Asia",
                 "lat": 36.79, "lon": 31.44, "fire": "2021-07-28"},
    "uiseong": {"name": "Uiseong, North Gyeongsang", "country": "South Korea",
                "continent": "Asia", "lat": 36.35, "lon": 128.70, "fire": "2025-03-22"},
    "ofunato": {"name": "Ōfunato, Iwate", "country": "Japan",
                "continent": "Asia", "lat": 39.06, "lon": 141.72, "fire": "2025-02-26"},
    # Oceania
    "kangaroo-island": {"name": "Kangaroo Island, South Australia", "country": "Australia",
                        "continent": "Oceania", "lat": -35.80, "lon": 137.20, "fire": "2019-12-20"},
}
