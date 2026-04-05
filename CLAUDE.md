# Development Guidelines

## General

- Read `DEVELOPMENT.md` for coding conventions,
  architecture, naming rules, and testing instructions.
  Follow these conventions in all new and modified code.

- Read all **/README* files for more guidelines.

- Before modifying any code or utilities, check for associated tests
  and run those tests to get a baseline before making any changes.
  Flag any testing problems before implementing planned changes.

- After modifying any code or utility, always run the associated tests
  before considering any changes complete.

- Always look at the contents of a script to see what kind of script
  it is. Do not rely on file name extensions (or lack thereof).
  Scripts that have `uv run --script` in their shebang are Python
  scripts, not shell scripts (regardless of the file extension).
