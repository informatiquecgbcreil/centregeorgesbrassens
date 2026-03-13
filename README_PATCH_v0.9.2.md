# Patch v0.9.2 — Stabilisation ciblée

## Correctifs inclus
- inventaire : création d'items depuis facture passée en POST + permission `inventaire:edit`
- suppression des garde-fous morts `if False: abort(403)` dans les routes inventaire
- accès public explicite au flux de réinitialisation de mot de passe avant setup complet
- durcissement des uploads du passeport pédagogique : extensions autorisées + limite de taille + téléchargement sécurisé si fichier absent
- templates mis à jour pour utiliser des formulaires POST avec CSRF
- zip de diffusion nettoyé des fichiers fantômes et des données d'instance
- script de fiabilité renforcé pour vérifier le reset password même sur base fraîche

## À tester en priorité
Voir la réponse ChatGPT associée au patch.
