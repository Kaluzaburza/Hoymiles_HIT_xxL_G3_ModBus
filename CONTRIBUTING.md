# Contributing / Współtworzenie

Thank you for helping improve a community project for home energy users.

## Before submitting a pull request

1. Do not copy proprietary vendor code, leaked documentation, or code under an
   incompatible license.
2. Run `python tools/validate_release.py` and the relevant optimizer tests.
3. For changes to EMS planning, run
   `python tools/test_automation_matrix.py`; before a release also run it with
   `--exhaustive`.
4. Describe the inverter model, firmware and test conditions. Never include
   passwords, API keys, serial numbers or personal energy data.
5. Sign every commit with `git commit -s` to certify the Contribution
   Certificate shown below.

## Contribution terms

By intentionally submitting a contribution to this repository, you confirm
that:

- you have the right to submit it under the repository's current
  [MIT License](LICENSE);
- the contribution may be used, modified and distributed under MIT, including
  for commercial purposes;
- you retain your copyright and any attribution required by law;
- third-party material is clearly identified with its source and license.

If you do not agree, do not submit a pull request; open an issue with a
description instead.

## Contribution Certificate 1.0

By making a contribution to this project, I certify that:

(a) I created the contribution and may submit it under the MIT License, or I
have documented permission from every relevant rights holder; and

(b) I identified every third-party part and its license, and those terms are
compatible with this repository; and

(c) I understand that the contribution and my sign-off are public records that
may be kept and redistributed with the project; and

(d) I accept the Contribution Terms above.

Add this line to each commit, using your real name and email:

`Signed-off-by: Full Name <email@example.com>`

---

Dziękujemy za rozwijanie społecznościowego projektu dla domowych użytkowników
energii. Nie przesyłaj kodu producenta, wyciekłych dokumentów ani materiałów z
niezgodną licencją. Uruchom walidator i odpowiednie testy, opisz warunki próby
oraz podpisz commity przez `git commit -s`.

Przesyłając świadomie wkład, potwierdzasz prawo do udostępnienia go na
[licencji MIT](LICENSE). Wkład może być używany, modyfikowany i
rozpowszechniany na zasadach MIT, również komercyjnie. Zachowujesz swoje prawa
autorskie i należne oznaczenie autorstwa, a materiały osób trzecich muszą mieć
podane źródło oraz zgodną licencję.
