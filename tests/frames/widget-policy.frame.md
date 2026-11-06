---
type: frame [0.2]
name: widget-policy
description: Test-only policy for widget and gadget requests, used by the workflow test suite.
visibility: internal
version: "1.0.0"
scope: department
maintainer: tests
inherits: org-model
---
# Widget policy (test fixture)

This Frame exists only for the test suite. It declares its own roles,
members and folders so it never depends on who the company Frames name.

## Roles
- widget-requester
- widget-checker
- widget-approver
- widget-supervisor

## Members
- erin (person): widget-requester, widget-checker
- frank (person): widget-checker
- gina (person): widget-requester
- hank (person): widget-approver
- jack (person): widget-approver
- ivy (person): widget-supervisor

## Folders
- Widgets
- Widgets/Requests
- Widgets/Gadgets

## Permissions
Widget requesters may create widgets in Widgets/Requests.
Widget requesters may read Widgets.
Widget checkers may approve widgets in Widgets/Requests.
Widget checkers may read Widgets.
Widget approvers may approve widgets in Widgets/Requests.
Widget approvers may read Widgets.
Widget supervisors may approve widgets in Widgets/Requests.
Widget supervisors may read Widgets.
Widget requesters may create gadgets in Widgets/Gadgets.
Widget approvers may approve gadgets in Widgets/Gadgets.

## Thresholds
The widget review interval is 14 days.

## Routing
At Check the widget in widget-request go to the assignee.
Approvals in widget-request go to the widget approver.
Overdue items in widget-request go to the widget supervisor.
Check the widget in widget-request is due within 3 days.
Approvals in widget-request are due within 5 days.
Approvals in gadget-request go to the widget approver.

## Process: widget-request
Records are filed in Widgets/Requests.
The record type is task.
Closes as done.
Rejections return to Check the widget.
1. [widget-requester] Request the widget (record-filer)
2. [widget-checker] Check the widget
3. [system] Link the widget (record-linker)
4. [widget-approver] Approve the widget

## Process: gadget-request
Records are filed in Widgets/Gadgets.
The record type is task.
Closes as done.
1. [widget-requester] Request the gadget (record-filer)
2. [widget-approver] Approve the gadget
