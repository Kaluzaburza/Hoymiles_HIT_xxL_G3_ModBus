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
that you have the right to submit it and agree that:

- the contribution may be distributed under the repository's current
  [PolyForm Noncommercial License 1.0.0](LICENSE);
- you grant the repository owner a perpetual, worldwide, non-exclusive,
  irrevocable, royalty-free copyright license to use, reproduce, modify,
  distribute, sublicense and relicense the contribution, including under a
  separate commercial license;
- you retain your copyright and any attribution required by law;
- third-party material is clearly identified with its source and license.

These contribution terms keep the community version available for
noncommercial users while allowing the project owner to enforce and manage a
consistent licensing policy. If you do not agree, do not submit a pull request;
open an issue with a description instead.

## Contribution Certificate 1.0

By making a contribution to this project, I certify that:

(a) I created the contribution and may submit it under the Contribution Terms
above, or I have documented permission from every relevant rights holder; and

(b) I identified every third-party part and its license, and those terms are
compatible with this repository; and

(c) I understand that the contribution and my sign-off are public records that
may be kept and redistributed with the project; and

(d) I accept the Contribution Terms above, including the license and relicensing
grant to the repository owner.

Add this line to each commit, using your real name and email:

`Signed-off-by: Full Name <email@example.com>`

---

Dziękujemy za rozwijanie społecznościowego projektu dla domowych użytkowników
energii. Nie przesyłaj kodu producenta, wyciekłych dokumentów ani materiałów z
niezgodną licencją. Uruchom walidator i odpowiednie testy, opisz warunki próby
oraz podpisz commity przez `git commit -s`.

Przesyłając świadomie wkład, potwierdzasz prawo do jego udostępnienia, zgadzasz
się na dystrybucję na aktualnej licencji projektu i udzielasz właścicielowi
repozytorium niewyłącznej, bezterminowej, ogólnoświatowej i nieodpłatnej zgody
na używanie, modyfikowanie, dystrybucję, sublicencjonowanie i zmianę licencji
tego wkładu, również w ramach osobnej licencji komercyjnej. Zachowujesz swoje
prawa autorskie i należne oznaczenie autorstwa.
