---
type: frame [0.2]
name: quality-manual
description: "Quality manual of {{COMPANY_NAME}}: context, scope, quality policy, roles, risks and objectives, resources, communication, operational planning and improvement. Narrative only; the SOP Frames carry the rules."
visibility: internal
version: "0.1.0"
scope: company
maintainer: "{{MANAGEMENT_REPRESENTATIVE}}"
inherits: org-model
clauses: ["4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3", "6.1", "6.2", "7.1", "7.4", "8.1", "10.1", "10.3"]
compile: none
---
# Quality manual

This manual describes the quality management system (QMS) of {{COMPANY_NAME}}
against ISO 9001:2015. It is narrative: the compiler extracts no rules from
it. The rules live in the SOP Frames that inherit the organization model, and
every rule in the policy release cites the sentence it came from. Replace the
`{{PLACEHOLDER}}` tokens before the first management review.

## 4.1 Understanding the organization and its context

{{COMPANY_NAME}} designs, makes and supports {{PRODUCTS_AND_SERVICES}} for
{{MARKETS}}. The external and internal issues relevant to its purpose and to
the intended results of the QMS are reviewed at each management review
(see the management-review SOP) and kept in the Quality/Management Review
folder as the context register. Issues include {{CONTEXT_ISSUES}}.

## 4.2 Understanding the needs and expectations of interested parties

The interested parties and their requirements are: customers
({{CUSTOMER_REQUIREMENTS}}), regulators ({{REGULATORY_REQUIREMENTS}}),
suppliers and external providers (clear specifications and timely payment,
see the supplier-evaluation SOP and the finance policy), employees
(competence, awareness, a safe workplace) and owners ({{OWNER_EXPECTATIONS}}).
The list is reviewed at management review.

## 4.3 Determining the scope of the quality management system

The QMS applies to {{SCOPE_STATEMENT}} at {{SITES}}. Exclusions:
{{EXCLUSIONS}} (for example clause 8.3 where no design is performed), with
the justification recorded here. The scope statement is a controlled document
in Quality/Controlled Documents.

## 4.4 Quality management system and its processes

The QMS is a set of processes, each described by an SOP Frame and compiled
into a workflow in the policy release: document control, training,
nonconformance, corrective action (CAPA), internal audit, supplier
evaluation, change control, management review, calibration, complaints and
tasks. For each process the Frame states where records are filed, the steps,
who performs each step, who approves, within what time, and what a cog may
decide alone. The release bundle is the process map: its routing table shows
the interactions, its SLAs the performance criteria, and every run's Track is
the evidence that the process ran as written.

## 5.1 Leadership and commitment

Top management ({{TOP_MANAGEMENT}}) is accountable for the effectiveness of
the QMS. It approves the quality policy and objectives, provides the
resources in section 7.1, chairs the management review, and holds the
`policy-owner` role: no change to a policy Frame takes effect until a policy
owner has approved it at the Gate, and a change that widens access needs two.
Customer focus (5.1.2): customer requirements and applicable statutory and
regulatory requirements are determined and met; customer complaints are
logged and risk-scored in the complaint process, and customer satisfaction is
an input to management review.

## 5.2 Policy

Quality policy of {{COMPANY_NAME}}: {{QUALITY_POLICY}}. The policy is
appropriate to the purpose and context of the organization, provides a
framework for the quality objectives, commits to satisfying applicable
requirements and to continual improvement. It is communicated by being a
controlled document that every member of staff acknowledges through the
training process, and it is available to interested parties on request.

## 5.3 Organizational roles, responsibilities and authorities

Roles are declared in the organization model Frame; every member holds one or
more. The management representative ({{MANAGEMENT_REPRESENTATIVE}}) reports
on the performance of the QMS, prepares and records the management review,
approves suppliers and receives escalations. The quality manager
({{QUALITY_MANAGER}}) dispositions nonconformances, owns corrective actions,
accepts audit findings and reviews calibrations. Internal auditors plan and
perform audits of processes they do not own. The document controller files
controlled documents and chases overdue reviews. Process owners verify the
corrective actions in their area and approve drawings. Staff raise
nonconformances, complaints, change requests and tasks, and acknowledge the
documents assigned to them. Cogs hold roles like any member and approve only
within the criteria written in the Frames.

## 6.1 Actions to address risks and opportunities

Risks and opportunities are identified from the context (4.1), the
interested parties (4.2), audit findings, nonconformance trends and
complaints, and recorded in the risk register ({{RISK_REGISTER_LOCATION}}).
Each item names an owner, the action, and when it is reviewed. Run-level risk
is scored automatically on every nonconformance, complaint, change request
and controlled document; the score decides the route to a person or to a cog.

## 6.2 Quality objectives and planning to achieve them

The quality objectives for {{YEAR}} are: {{OBJECTIVES}}. Each objective is
measurable, has an owner, resources, a due date and a method of evaluation,
and is tracked as a task in the Tasks folder. Progress is an input to
management review.

## 7.1 Resources

People (7.1.2): the roles above are staffed by the members in the
organization model. Infrastructure (7.1.3): {{INFRASTRUCTURE}}. Environment
(7.1.4): {{WORK_ENVIRONMENT}}. Monitoring and measuring resources (7.1.5):
measuring equipment is calibrated per the calibration SOP; an out-of-tolerance
result opens a nonconformance automatically. Organizational knowledge
(7.1.6): SOPs, work instructions and records are controlled documents and
remain available across staff changes.

## 7.4 Communication

Internal communication runs through the hub: routed approvals and
escalations land in the inbox of the person responsible, every record shows
its state and who holds it, and "Why" on any decision shows the policy
sentence behind it. External communication with customers goes through
{{CUSTOMER_CHANNELS}} and with suppliers through {{SUPPLIER_CHANNELS}}.

## 8.1 Operational planning and control

Processes are planned by writing them as SOP Frames and compiling them: a
process that is not in the policy release cannot run. Criteria for
acceptance, the records to keep and their retention are set by the record
type definitions pinned in each release. Outsourced processes are controlled
through supplier evaluation.

## 10.1 Improvement

Opportunities for improvement come from complaints, nonconformances, audit
findings, calibration results, the context review and staff suggestions
(filed as tasks). Each is handled in the process named for it, and the result
is reviewed at management review.

## 10.3 Continual improvement

The suitability, adequacy and effectiveness of the QMS are evaluated at each
management review from the inputs in 9.3.2. Where the analysis shows a need,
the outputs name the improvement, its owner and its due date, and a change
request or CAPA carries it out.

## Roles and responsibilities
| Role | Responsibilities |
|---|---|
| Management representative | Keeps the quality management system and reports on its performance to top management. |
| Quality manager | Owns the quality processes the manual describes and keeps them in line with ISO 9001. |
| Staff | Knows the quality policy, the objectives that concern their work and where each process is described. |
