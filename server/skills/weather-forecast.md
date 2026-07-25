---
name: weather-forecast
description: Structured weather reply with icons, temperature ranges, and day-part breakdown.
category: general
enabled: true
triggers:
  keywords:
    - weather
    - forecast
    - temperature
    - rain
    - snow
    - sunny
    - cloudy
    - humidity
    - wind
    - storm
    - climate
    - degrees
    - meteo
    - hot today
    - cold today
    - will it rain
    - is it going to rain
---

When the user asks about weather, structure your answer as:

1. **Now** — one line: current conditions + temperature + a matching icon
   (`:sun:` / `:cloud-sun:` / `:cloud:` / `:cloud-rain:` / `:cloud-snow:` /
   `:cloud-lightning:` / `:fog:`) and a `:thermometer:` for temperature.
2. **Today** — high/low, chance of precipitation, wind if notable.
3. **Tomorrow** — one line, same format as Today.
4. **Rest of the week** — a short bullet list or an inline sentence if it's
   uneventful.

Guidelines:

- Always mention the city/region you're describing. If the user didn't
  specify one, ask — don't guess a location.
- Use icons inline with the words they modify, e.g. `:cloud-rain: heavy rain`
  or `:wind: gusting to 40 km/h`.
- Prefer `:sunrise:` / `:sunset:` for daylight questions.
- If you don't have live data (no web-search tool available or the tool
  returned nothing), say so plainly and give **general climate expectations**
  for the location and season instead of fabricating numbers.
- Never invent specific temperatures, precipitation percentages, or wind
  speeds without a source.
- Keep the whole answer under ~150 words unless the user asked for a
  detailed multi-day breakdown.
