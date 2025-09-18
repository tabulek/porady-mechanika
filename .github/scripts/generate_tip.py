import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

response = openai.chat.completions.create(
    model="gpt-3.5-turbo",
    messages=[
        {"role": "system", "content": "Jesteś doświadczonym mechanikiem samochodowym. Generuj krótką poradę dla kierowców dotyczącą codziennej eksploatacji samochodu."},
        {"role": "user", "content": "Podaj poradę dla kierowcy na dziś."}
    ],
    max_tokens=100
)

tip = response.choices[0].message.content.strip()

with open("porady.txt", "a", encoding="utf-8") as f:
    f.write(tip + "\n")
