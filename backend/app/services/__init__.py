"""
Services package — business-logic layer for the Fabrique backend.

Each context is a subpackage (``services/<context>/``); import a module from
it by its full path (``from services.integrations.outbox_service import
process_outbox``). The package itself imports nothing: every ``services.*``
import runs this file, so a re-export here would load its module, and all it
imports, for every importer.
"""
