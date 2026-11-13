---
type: frame [0.2]
name: tasks-sop
description: "Tasks: any member of staff files a task for a person; it waits for that person and closes as done when they complete it. Used for quality objectives, management review actions and improvement suggestions."
visibility: internal
version: "0.1.2"
scope: company
maintainer: operations
inherits: org-model
clauses: []
compile: convention
---
# Tasks SOP

A task is the general-purpose record for work that is not one of the quality
processes: an action from management review, a quality objective milestone,
an improvement suggestion, a reminder. Any member of staff files a task for
an assignee (themselves by default); the task waits for the assignee and
closes as done when they complete it. Tasks live in the Tasks folder and are
readable by everyone. A task may be filed for any cog too: it does the work where
it and the person it works for may, or hands the task back saying why it cannot.

## Permissions
Staff may create tasks in Tasks.
Staff may approve tasks in Tasks.
Staff may read Tasks.
The cogs group may approve tasks in Tasks.
The cogs group may read Tasks.

## Routing
At Complete the task in task go to the assignee.

## Process: task
Records are filed in Tasks.
The record type is task.
Closes as done.
1. [staff] File the task (record-filer)
2. [staff] Complete the task

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Staff | Files tasks for themselves and others and completes the tasks assigned to them. |
| the cogs group | Completes the tasks assigned to them, or hands one back saying why it cannot. |
