# Internal Product Risk Standard 2.3 (synthetic corpus v14)

> Synthetic internal standard for demonstration. Not the policy of any real institution.

## Section 1 Sign-off

### PR23-1.1 Product owner accountability for launch risk assessment

The bank shall require the accountable product owner to complete a documented financial crime risk assessment before any new product is submitted for approval. The assessment shall describe the intended customers, the delivery channels, the geographies served and the expected transaction behaviour. The product owner shall personally attest that the assessment is accurate and complete, and no launch request shall proceed without that attestation.

### PR23-1.2 Independent compliance review before approval

The bank shall ensure that every launch request receives an independent review by the financial crime compliance function before final approval. The reviewer shall be free of commercial targets tied to the product, shall challenge the stated risk ratings, and shall record a clear recommendation to approve, approve with conditions, or decline. The business shall not override a decline recommendation without escalation to the designated senior risk committee.

### PR23-1.3 Conditional approvals and remediation deadlines

Where the bank grants approval subject to conditions, the bank shall state each condition in writing, assign a named owner and set a firm completion date. Conditions that address control gaps shall be closed before launch unless the senior risk committee accepts a documented interim measure. The bank shall track open conditions to closure and shall suspend further rollout where a deadline is missed without an approved extension.

### PR23-1.4 Sign-off for material changes to live products

The bank shall treat a material change to a live product as requiring fresh sign-off, even when the original launch was approved. Material changes include new features, altered transaction limits, new customer segments, additional delivery channels and changes to supporting processes. The product owner shall submit a change assessment, and the compliance function shall decide whether a full or abbreviated review is needed before the change goes live.

### PR23-1.5 Retention of sign-off evidence and periodic reattestation

The bank shall retain all sign-off records, including assessments, review notes, conditions and final decisions, in a central repository that is accessible to audit and regulators on request. Product owners shall reattest to the continued accuracy of each assessment at least annually. Where the attestation cannot be given, the owner shall notify the compliance function, which shall decide whether the product must be reviewed again.

## Section 2 Limits and velocity

### PR23-2.1 Setting launch limits for new products

The bank shall define transaction, daily, and balance limits for every new product before launch, based on the product's inherent risk assessment and the expected behaviour of its target customers. Limits shall be documented with a written rationale, approved by the product owner and the financial crime risk function, and configured in the system of record. No product shall go live with unlimited or undefined thresholds.

### PR23-2.2 Velocity thresholds for rapid fund movement

The bank shall configure velocity thresholds that measure the number and value of incoming and outgoing transfers over defined rolling periods, including short windows of hours and days. Products that allow funds to be received and sent onward quickly shall carry tighter thresholds. Breaches shall generate alerts for review, and the thresholds shall be tested before any change to transfer speed or payment features is released.

### PR23-2.3 Controls on customer limit increase requests

The bank shall treat every request to raise a customer's limits as a risk event. Before approving an increase, the bank shall confirm the customer's identity, review account activity and source of funds against the stated purpose, and consider adverse information. Increases for higher-risk customers shall require enhanced review and senior approval, and each decision shall be recorded with its justification.

### PR23-2.4 Lower limits for higher-risk geographies

The bank shall apply reduced transaction and velocity limits to payments involving countries or corridors rated higher risk under the bank's geographic risk methodology. Before the bank enters a new geography, it shall set corridor-specific limits and review them after the first months of live activity. Limits shall not be relaxed for a corridor without documented approval from the financial crime risk function.

### PR23-2.5 Limit recalibration and override governance

The bank shall review the effectiveness of limits and velocity thresholds at least annually, and after any material change in product design, channel, or customer mix, using alert volumes, investigation outcomes, and observed behaviour. Manual overrides of limits shall be exceptional, time-bound, and approved by an authorised officer. All overrides shall be logged and reported periodically to risk governance for oversight.

## Section 3 Disclosure

### PR23-3.1 Disclosure of Product Limits and Restrictions

The bank shall disclose to customers, before account opening or first use, the transaction limits, eligibility conditions and permitted uses that apply to each product. Product owners shall document these restrictions in plain language and confirm that the disclosures match the controls configured in the systems. Any product launched without reviewed disclosures of its financial-crime-related restrictions shall not be released to customers until Compliance has approved the wording.

### PR23-3.2 Corridor-Specific Disclosure for Cross-Border Transfers

The bank shall give customers clear disclosure of the destination corridors, additional verification steps, expected processing delays and possible holds that apply to cross-border transfers. Where a corridor carries elevated risk, the disclosure shall state that extra information about the sender, recipient or purpose may be requested. Product owners shall review corridor disclosures whenever a new destination is added or an existing corridor's risk rating changes.

### PR23-3.3 Customer Notice of Monitoring and Reporting

The bank shall inform customers in its account terms that activity is monitored for financial-crime purposes and that information may be shared with competent authorities where required by law. Notices shall be worded generally and shall never reveal whether a specific customer is the subject of an alert, review or report. Staff shall use approved scripts when customers ask about monitoring, so that no inappropriate detail is disclosed.

### PR23-3.4 Notification of Material Changes to Product Terms

The bank shall notify affected customers in advance of any material change to product terms that alters limits, verification requirements, permitted activity or the conditions for account restriction. Product owners shall assess each change for financial-crime impact before the notice is issued and keep a record of the assessment, the approved wording and the date customers were notified. Urgent changes made for risk reasons shall be explained as soon as practicable afterwards.

### PR23-3.5 Consistency of Partner-Facing Customer Disclosures

The bank shall require any vendor or partner that presents the bank's products to customers to use disclosures that are consistent with the bank's approved wording on restrictions, verification and monitoring. Before onboarding, the business owner shall review the partner's customer-facing screens, scripts and documents across every channel. Agreements shall oblige the partner to obtain approval before changing those materials and to supply evidence of the versions in use on request.
