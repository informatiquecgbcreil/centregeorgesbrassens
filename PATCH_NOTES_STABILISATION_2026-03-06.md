# Stabilisation v0.9.1

## Correctifs inclus
- actions sensibles passées en POST + CSRF + permissions explicites
- ouverture/fermeture kiosque en POST
- génération d'archives collectives en POST
- création inventaire depuis facture en POST
- finalisation mensuelle individuelle en POST
- page mot de passe oublié accessible avant setup complet
- smoke tests fiabilisés pour le reset password
- package de diffusion nettoyé : pas de `.env`, pas d'`instance/` réelle, pas de fichiers `Copie` / `.py1` / `.html2`

## Dossiers d'instance créés à vide
- `instance/archives_emargements`
- `instance/archives_pedagogie`
- `instance/docx_templates`
- `instance/signatures_tmp`
- `static/uploads`
