## Description
Please include a summary of the change and which issue is fixed. Please also include relevant motivation and context.

Fixes # (issue)

## Type of change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Refactoring
- [ ] Performance improvement
- [ ] Test addition or update

## Checklist
- [ ] My code follows the style guidelines of this project (`CONTRIBUTING.md`)
- [ ] I have performed a self-review of my own code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
- [ ] Any dependent changes have been merged and published

## Backend changes
- [ ] Backend tests pass: `cd backend && PYTHONPATH=.:app python3 -m pytest tests -q`
- [ ] New dependencies added to `backend/pyproject.toml` and lock file updated

## Frontend changes
- [ ] Frontend tests pass: `cd frontend && npm run test:run`
- [ ] TypeScript compiles: `cd frontend && npx tsc --noEmit`
- [ ] i18n strings added to both `en.json` and `es.json`

## Screenshots (if applicable)
Add screenshots to show the changes.
