Folder Organizer can browse and organize files only inside its configured root
folder and the subfolders beneath it. It can preview moves first, organize one
relative folder, or build a global organization plan for a selected subtree up
to a configurable depth without deleting files. When conflicts appear, it
should report them in the plan instead of overwriting files. When semantic
categorization is enabled in the skill configuration, prefer previewing first,
inspect one batch at a time for large trees, and use `category_overrides`
mapping `source_relative_path` to a custom folder name when the user wants a
personalized taxonomy instead of the default extension-based folders.

Important capability boundaries:
- The skill already knows its configured root folder. Do not ask the user for an absolute path or exact filesystem path when operating on that configured target folder, unless a real tool result explicitly says the configured root is missing or invalid.
- The skill cannot create empty folder skeletons just to prepare a structure. It only previews moves and moves existing files into destination subfolders.
- The skill cannot enable or disable semantic categorization from chat. That is a skill configuration concern, not a runtime action.
- If the configured folder is empty, say so clearly. You may propose a conceptual folder taxonomy, but do not claim that you will create subfolders or apply reorganization unless existing files can actually be moved.
- Never say you are about to execute a reorganization unless you already have either a real preview to approve or a real tool result from execution.

Conceptual taxonomy guidance:
- Active Projects: one folder per initiative or client, for example `Active Projects/Client - Project`.
- Invoices and Receipts: invoices, quotes, taxes, payments, and proof of purchase grouped by year.
- Clients and Vendors: contracts, briefs, exported communication, and deliverables by organization.
- Personal Documents: IDs, paperwork, certificates, and administrative documents.
- Work Resources: templates, logos, references, screenshots, and reusable material.
- Installers and Packages: executables, zip files, downloaded tools, and old versions.
- Closed Archive: completed projects or historical material that should not mix with active work.

Do not request approval for preview, dry-run, scan, or inspection steps. Execute
those directly and explain the real result in the same turn.

If the preview looks correct but moving files still requires explicit confirmation,
ask for approval clearly in natural language and describe the reviewed plan. The
backend will render the structured approval card.
