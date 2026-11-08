"""The organization cog: it knows the organization (people and roles, folders, processes, who decides what) and sits
on the compile-frame committee with the QA manager, reading SOPs into rules. It signs nothing."""

from __future__ import annotations

from .cogs import ReviewerCog


class OrganizationCog(ReviewerCog):
    id = "organization"
    description = ("Organization team member: knows the organization's people, roles, folders and processes; on the compile-frame "
                   "committee with the QA manager it reads the SOPs into rules for review. It signs nothing.")
