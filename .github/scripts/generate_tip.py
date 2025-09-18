import os
import openai

openai.api_key = os.getenv("OPENAI_API_KEY")

response = openai.ChatCompletion.create(
    model="gpt-3.5-turbo",
    messages=[
        {"role": "system", "content": "Jesteś pomocnym AI mechanikiem samochodowym."},
        {"role": "user", "content": "Podaj jedną praktyczną poradę dotyczącą samochodu."}
    ],
    max_tokens=100
)

tip = response.choices[0].message.content.strip()

with open("porady.txt", "a", encoding="utf-8") as f:
    f.write(tip + "\n")
