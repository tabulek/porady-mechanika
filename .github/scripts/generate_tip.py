import openai
import os

openai.api_key = os.getenv("OPENAI_API_KEY")

PROMPT = (
    "Podaj praktyczną, zwięzłą poradę dla kierowców samochodów od mechanika. "
    "Porada powinna być nowa, konkretna i dotyczyć codziennej eksploatacji auta."
)

response = openai.Completion.create(
    model="text-davinci-003",
    prompt=PROMPT,
    max_tokens=50,
    temperature=0.7,
)

tip = response.choices[0].text.strip()

# Dodaj poradę na końcu pliku porady.txt
with open("porady.txt", "a", encoding="utf-8") as f:
    f.write(f"{tip}\n")

print(f"Dodano poradę: {tip}")