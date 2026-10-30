import type { Options } from 'semantic-release';

const config: Options = {
  branches: ['main'],
  repositoryUrl: 'https://github.com/Beancount-Trans/Beancount-Trans-Backend',
  tagFormat: '${version}',
  plugins: [
    [
      '@semantic-release/commit-analyzer',
      {
        preset: 'conventionalcommits'
      }
    ],
    [
      '@semantic-release/release-notes-generator',
      {
        preset: 'conventionalcommits'
      }
    ],
    [
      '@semantic-release/changelog',
      {
        changelogFile: 'CHANGELOG.md',
        changelogTitle: '# Changelog'
      }
    ],
    [
      '@google/semantic-release-replace-plugin',
      {
        replacements: [
          {
            files: ['project/settings/settings.py'],
            pattern: "'VERSION': '[^']+'",
            replacement: "'VERSION': '${nextRelease.version}'"
          }
        ]
      }
    ],
    [
      '@semantic-release/git',
      {
        assets: [
          'CHANGELOG.md',
          'project/settings/settings.py',
          'package.json',
          'package-lock.json'
        ],
        message:
          'chore(release): ${nextRelease.version}\n\n${nextRelease.notes}'
      }
    ],
    [
      '@semantic-release/github',
      {
        successComment: false
      }
    ]
  ]
};

export default config;

