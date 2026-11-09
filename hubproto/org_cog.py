"""The organization cogs. The junior organization cog knows the organization (people and roles, folders, processes,
who decides what) and answers questions about it day to day on the local model. The senior cogs (change control
11.1), bundled with a hosted model, sit on the compile-frame committee (defined on the ``senior-cogs`` group) and read
the SOPs into rules by consensus. None of them signs anything."""

from __future__ import annotations

from .cogs import ReviewerCog


class OrganizationCog(ReviewerCog):
    id = "organization"
    description = ("Organization team member: knows the organization's people, roles, folders and processes and answers questions "
                   "about them. It signs nothing.")


class SeniorOrganizationCog(ReviewerCog):
    id = "senior-organization"
    description = ("Senior organization cog: on the compile-frame committee with the senior QA manager it reads the SOPs into rules "
                   "for review, by consensus. It signs nothing.")


class SeniorQAManagerCog(ReviewerCog):
    id = "senior-qa-manager"
    description = ("Senior QA manager cog: on the compile-frame committee with the senior organization cog it reads the SOPs into "
                   "rules from the quality system's point of view, by consensus. It signs nothing.")


SENIOR_COGS = (SeniorOrganizationCog, SeniorQAManagerCog)
