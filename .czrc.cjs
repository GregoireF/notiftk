'use strict'

/**
 * czg configuration for the notiftk repository.
 * Mirrors @gregoiref/cz-config from the utils repo — same types, API-specific scopes.
 * Usage: npx czg  (or: just commit if justfile is added)
 *
 * @type {import('cz-git').UserConfig}
 */
module.exports = {
  messages: {
    type: 'Sélectionne le type de changement :',
    scope: 'Quel composant est concerné ? (optionnel)',
    customScope: 'Quel composant est concerné ?',
    subject: 'Description courte et impérative :\n',
    body: "Description longue (optionnel). Utilise '|' pour sauter une ligne :\n",
    breaking: 'BREAKING CHANGES (optionnel) :\n',
    footerPrefixesSelect: 'Issue liée (optionnel) :',
    customFooterPrefix: 'Préfixe issue :',
    footer: "Numéros d'issue (ex. #31, #34) :\n",
    confirmCommit: 'Confirmer ce commit ?',
  },
  types: [
    { value: 'feat',     name: '✨ feat:      Nouvelle fonctionnalité',                emoji: ':sparkles:' },
    { value: 'fix',      name: '🐛 fix:       Correction de bug',                      emoji: ':bug:' },
    { value: 'docs',     name: '📝 docs:      Documentation uniquement',               emoji: ':memo:' },
    { value: 'style',    name: '💄 style:     Format, sans changement fonctionnel',    emoji: ':lipstick:' },
    { value: 'refactor', name: '♻️  refactor:  Refactorisation sans fix ni feat',      emoji: ':recycle:' },
    { value: 'perf',     name: '⚡️ perf:      Amélioration des performances',          emoji: ':zap:' },
    { value: 'test',     name: '✅ test:      Ajout ou correction de tests',            emoji: ':white_check_mark:' },
    { value: 'build',    name: '📦 build:     Système de build ou dépendances',        emoji: ':package:' },
    { value: 'ci',       name: '🎡 ci:        Configuration CI/CD',                    emoji: ':ferris_wheel:' },
    { value: 'chore',    name: '🔨 chore:     Autres changements hors src/test',       emoji: ':hammer:' },
    { value: 'revert',   name: '⏪ revert:    Annuler un commit précédent',             emoji: ':rewind:' },
    { value: 'wip',      name: '🚧 wip:       Work in progress (ne pas merger)',       emoji: ':construction:' },
  ],
  scopes: [
    { value: 'api',      name: 'api:       Endpoints FastAPI (status, stream, watch)' },
    { value: 'sse',      name: 'sse:       Générateurs Server-Sent Events' },
    { value: 'webhooks', name: 'webhooks:  Système webhooks (poll, dispatch, HMAC)' },
    { value: 'auth',     name: 'auth:      Authentification et rate limiting' },
    { value: 'tiktok',   name: 'tiktok:    Détection live TikTok et cache' },
    { value: 'infra',    name: 'infra:     OpenTofu / Fly.io infrastructure' },
    { value: 'frontend', name: 'frontend:  Interface web (index.html)' },
    { value: 'ci',       name: 'ci:        GitHub Actions / workflows' },
    { value: 'docs',     name: 'docs:      Documentation et README' },
    { value: 'deps',     name: 'deps:      Mises à jour de dépendances' },
    { value: 'tests',    name: 'tests:     Tests unitaires et intégration' },
  ],
  useEmoji: true,
  emojiAlign: 'left',
  useAI: false,
  allowCustomScopes: true,
  allowEmptyScopes: true,
  customScopesAlign: 'bottom',
  allowBreakingChanges: ['feat', 'fix'],
  skipQuestions: ['footer'],
  upperCaseSubject: false,
  minSubjectLength: 3,
  confirmColorize: true,
}
