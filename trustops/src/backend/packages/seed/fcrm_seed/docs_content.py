"""The text of the three synthetic demo documents (ADR-29).

Pure data: :func:`document_specs` returns the business case, the payout partner
contract and the vendor due-diligence scope letter as pages of blocks, with each
fact the extractor should find declared next to the sentence or table row that
carries it. :mod:`fcrm_seed.docs_layout` renders it and predicts every fact's
page and line.

Everything here is invented: the bank, the partner (Remesas del Norte S.A. is the
walkthrough's own fictional partner), the audit firm and every named person. The
figures are the walkthrough-v2 seed's (``demo.fields``): $48M year 1, Q1 2027,
US to Mexico, MXN, 38 agent locations in 6 states, about 62,000 transactions a
month by month 6, KYC standard v4.

Facts are chosen so the documents agree with the demo, not with a wish list:

* the business case and the contract both say payout is **bank deposit only**
  (scene 6 flags the owner's cash-pickup answer as a new, contradicting fact);
* neither says anything about the partner's due-diligence history, licence
  number, AML attestation or sign-off (those are the open and answered fields);
* the three values reused from FCRM-2026-0118 (limits, disclosure text and
  monitoring rule set) appear nowhere;
* the cash-pickup network, the volume basis and KYC standard v4 appear only in
  the scope letter, the document the PO attaches in scene 11.
"""

from __future__ import annotations

from typing import Optional

from fcrm_seed.docs_layout import (
    STAGE_CLARIFICATION,
    STAGE_INTAKE,
    Block,
    DocumentSpec,
    Fact,
    Heading,
    Page,
    Paragraph,
    Table,
)

BUSINESS_CASE_FILE = "business_case_remittance_MX.pdf"
CONTRACT_FILE = "payout_partner_contract.pdf"
SCOPE_LETTER_FILE = "vendor_DD_scope_letter_2026.pdf"


def _fact(
    field_id: Optional[str],
    label: str,
    value: str,
    quote: str,
    seed_source: Optional[str] = None,
    stage: str = STAGE_INTAKE,
) -> Fact:
    return Fact(
        field_id=field_id,
        label=label,
        value=value,
        quote=quote,
        stage=stage,
        seed_source=seed_source,
    )


def _h(text: str) -> Heading:
    return Heading(text)


def _p(text: str, *facts: Fact) -> Paragraph:
    return Paragraph(text, tuple(facts))


def _t(
    header: tuple[str, ...],
    rows: list[tuple[str, ...]],
    widths: tuple[float, ...],
    facts: Optional[dict[int, tuple[Fact, ...]]] = None,
) -> Table:
    return Table(header, tuple(rows), widths, facts or {})


def _page(*blocks: Block) -> Page:
    return Page(blocks)


# ------------------------------------------------------------- business case
def _business_case_front() -> list[Page]:
    """Pages 1 to 5: summary, rationale, product, corridor, operating model."""
    page1 = _page(
        _t(
            ("Item", "Detail"),
            [
                (
                    "Change description",
                    "Consumer remittances US to Mexico through payout partner "
                    "Remesas del Norte S.A.",
                ),
                ("Change owner", "Priya Nair, Payments"),
                ("Business unit", "Payments"),
                ("Executive sponsor", "Head of Payments Products"),
                ("Document status", "Draft for FCRM change assessment, version 1.0"),
            ],
            (126.0, 378.0),
            {
                0: (
                    _fact(
                        "F02",
                        "Change description",
                        "Consumer remittances US to Mexico through payout partner "
                        "Remesas del Norte S.A.",
                        "Consumer remittances US to Mexico through payout partner "
                        "Remesas del Norte S.A.",
                        "case p.1 l.3",
                    ),
                ),
                1: (
                    _fact(
                        "F01",
                        "Change owner",
                        "Priya Nair, Payments",
                        "Priya Nair, Payments",
                        "case p.1 l.2",
                    ),
                ),
            },
        ),
        _p(
            "Target go-live is Q1 2027, subject to completion of the FCRM change "
            "assessment, partner integration testing and the readiness sign-offs "
            "listed in section 11. The Payments unit proposes to launch consumer "
            "remittances from the United States to Mexico through a third-party "
            "payout partner, which will credit beneficiaries in Mexico.",
            _fact(
                "F03",
                "Go-live date",
                "Q1 2027",
                "Target go-live is Q1 2027",
                "case p.1 l.9",
            ),
        ),
        _h("1. Executive summary"),
        _p(
            "The Bank has no consumer remittance product today. Retail customers "
            "who send money to family in Mexico use other providers, and the Bank "
            "loses the related deposit and card relationships. This case asks for "
            "approval to offer a digital send service to existing retail "
            "customers, with payout delivered in Mexico by a partner."
        ),
        _p(
            "The service is intended to be simple and low value. Customers send "
            "from the Bank's mobile app or web banking, pay a transparent fee and "
            "the beneficiary receives the funds in a Mexican bank account. The "
            "Bank does not hold money for the beneficiary and does not operate "
            "any payout locations of its own."
        ),
        _p(
            "This document is the business case submitted for FCRM change "
            "assessment. It sets out the rationale, the proposed operating model, "
            "the volume and revenue projections, the risks the business has "
            "identified and the controls it proposes. Sections 9 and 10 are "
            "written for the FCRM analyst and the Risk Committee."
        ),
    )
    page2 = _page(
        _h("2. Business rationale and target market"),
        _p(
            "Mexico is one of the largest remittance corridors for United States "
            "senders, and a large share of that activity is still carried out "
            "through non-bank providers. The Bank's retail franchise includes "
            "many customers who already send money home each month, using cash "
            "at agent locations or other digital services."
        ),
        _t(
            ("Segment", "In scope", "Notes"),
            [
                (
                    "Small business customers",
                    "No",
                    "Business payments are handled by Treasury Services",
                ),
                (
                    "Retail consumers",
                    "Yes",
                    "Existing deposit customers sending money to family in Mexico",
                ),
                (
                    "High-net-worth individuals",
                    "No",
                    "Served by Private Banking international transfers",
                ),
                (
                    "Non-customers",
                    "No",
                    "The service is limited to fully onboarded customers",
                ),
            ],
            (150.0, 60.0, 294.0),
            {
                1: (
                    _fact(
                        "F04",
                        "Customer segment",
                        "Retail consumers",
                        "Retail consumers",
                        "case p.2 l.8",
                    ),
                )
            },
        ),
        _p(
            "Customer research carried out by the Payments unit in the second "
            "quarter found that around one in eight retail customers sends money "
            "abroad at least quarterly, and that Mexico is the most frequently "
            "named destination. Customers cited fees, delivery time and trust in "
            "the provider as the main reasons for choosing a service."
        ),
        _p(
            "The proposal targets one segment only. Eligibility is enforced at "
            "the point of sale: only customers who have completed the Bank's "
            "standard customer due diligence, have held a checking account for "
            "at least ninety days and have no open fraud flags can use the "
            "service."
        ),
        _p(
            "The strategic case is retention and primary-bank engagement. "
            "Customers who send money regularly are more likely to hold direct "
            "deposit and a debit card with the Bank, and a competitive "
            "remittance service protects those balances."
        ),
    )
    page3 = _page(
        _h("3. Product description"),
        _p(
            "The service allows an eligible customer to send a single remittance "
            "from a Bank checking account to a named beneficiary in Mexico. The "
            "customer enters the beneficiary's name and bank account details, "
            "reviews the fee and the exchange rate shown before confirming, and "
            "receives a confirmation and a receipt by email and in the app."
        ),
        _p(
            "Customers send through the Bank's mobile app and web banking. "
            "Branch and call-centre assisted sending are out of scope for the "
            "first release. Each transfer is sent in US dollars and delivered in "
            "Mexican pesos at the exchange rate quoted to the customer at the "
            "time of the transfer.",
            _fact(
                None,
                "Sending channels",
                "Mobile app and web banking",
                "Customers send through the Bank's mobile app and web banking",
            ),
        ),
        _h("3.1 Customer experience"),
        _p(
            "The send flow is designed to take under three minutes. Customers "
            "see the total cost, including the fee and any exchange-rate margin, "
            "before they confirm, and can cancel within the period permitted for "
            "the transfer. Status updates are shown in the app until the "
            "beneficiary has been paid."
        ),
        _p(
            "Support for the service sits with the existing digital banking "
            "service desk, which will receive scripts and training before "
            "launch. Complaints and error resolution follow the Bank's standard "
            "process for payment disputes."
        ),
    )
    page4 = _page(
        _h("4. Corridor and payout partner"),
        _p(
            "The service launches in a single corridor. Funds are sent from the "
            "Bank's customers in the United States to beneficiaries in Mexico; "
            "no other sending or receiving country is in scope. The Payments "
            "unit will assess any later extension of the corridor as a separate "
            "change."
        ),
        _p(
            "The Bank proposes to appoint Remesas del Norte S.A. as its payout "
            "partner in Mexico. The partner receives the transfer instruction "
            "and the settlement funds from the Bank and credits the "
            "beneficiary's account. The partner already supplies payment "
            "services to the Bank in the United States; this case extends that "
            "relationship to the Mexico corridor."
        ),
        _p(
            "The partner was selected after a competitive review of three "
            "providers on price, delivery speed, bank coverage in Mexico and "
            "ease of technical integration. The selection paper is held by the "
            "Payments unit and is available to the FCRM analyst on request."
        ),
        _t(
            ("Item", "Detail"),
            [
                ("Sending market", "United States, existing retail customers only"),
                ("Corridor", "US to Mexico, one way, sending from the US only"),
                ("Beneficiary", "Individuals in Mexico with a bank account"),
                ("Delivery", "Credit to the beneficiary account, typically same day"),
            ],
            (150.0, 354.0),
            {
                1: (
                    _fact(
                        "F20",
                        "Corridor countries",
                        "US to Mexico",
                        "US to Mexico, one way",
                        "case p.4 l.12",
                    ),
                )
            },
        ),
        _p(
            "Funds flow only one way. The Bank does not receive remittances from "
            "Mexico under this proposal, and the partner does not take deposits "
            "or hold customer funds beyond the prefunded settlement balance."
        ),
    )
    page5 = _page(
        _h("5. Operating model and payout channels"),
        _p(
            "The end-to-end flow has five steps. The customer submits the "
            "transfer in the app; the Bank screens the parties, applies its "
            "transaction monitoring and debits the customer; the Bank sends the "
            "payout instruction to the partner; the partner credits the "
            "beneficiary's bank account in Mexico; and the partner confirms "
            "delivery, after which the Bank closes the transaction."
        ),
        _p(
            "Payout is by bank deposit only. The partner credits the "
            "beneficiary's account at a Mexican bank; there is no mobile wallet "
            "and no home delivery in the first release, and no other payout "
            "channel is proposed.",
            _fact(
                "F21",
                "Payout channels",
                "Bank deposit only",
                "Payout is by bank deposit only",
            ),
        ),
        _p(
            "Settlement between the Bank and the partner is made in advance on a "
            "prefunded basis. The Bank keeps a balance with the partner that "
            "covers the next business day's expected payouts and tops it up "
            "each day."
        ),
        _h("5.1 Operational responsibilities"),
        _p(
            "The Bank owns customer onboarding, sanctions and transaction "
            "screening, transaction monitoring and customer support. The partner "
            "owns beneficiary account verification, payout execution, delivery "
            "confirmation and its own regulatory obligations in Mexico."
        ),
        _p(
            "Operational reporting from the partner is daily: payouts "
            "completed, payouts returned and exceptions raised. Reconciliation "
            "is performed by Payments Operations each business day, and "
            "unreconciled items older than two days are escalated."
        ),
    )
    return [page1, page2, page3, page4, page5]


def _business_case_middle() -> list[Page]:
    """Pages 6 to 10: volumes, market, regulation, risks, controls."""
    page6 = _page(
        _h("6. Volume and financial projections"),
        _p(
            "Volume projections assume a gradual ramp-up as the service is "
            "promoted to eligible customers. The assumptions below were "
            "prepared by the Payments unit with Finance and have not yet been "
            "challenged by FCRM; the change assessment will test them against "
            "comparable launches."
        ),
        _p("Table 1: Key planning assumptions"),
        _t(
            ("Assumption", "Value"),
            [
                ("Eligible retail customers at launch", "About 410,000"),
                (
                    "Expected adoption by end of year 1",
                    "Around 6 percent of eligible customers",
                ),
                ("Launch sending states", "Texas, California and Illinois"),
            ],
            (230.0, 274.0),
            {
                2: (
                    _fact(
                        None,
                        "Launch sending states",
                        "Texas, California and Illinois",
                        "Texas, California and Illinois",
                    ),
                )
            },
        ),
        _p("Table 2: Projected annual send volume and fee revenue"),
        _t(
            ("Year", "Send volume", "Fee revenue"),
            [
                ("Year 1 (2027)", "$48M", "$1.9M"),
                ("Year 2 (2028)", "$85M", "$3.2M"),
                ("Year 3 (2029)", "$120M", "$4.3M"),
            ],
            (200.0, 152.0, 152.0),
            {
                0: (
                    _fact(
                        "F05",
                        "Expected annual volume",
                        "$48M year 1",
                        "Year 1 (2027) | $48M",
                        "case p.6 tbl 2",
                    ),
                    _fact(
                        None,
                        "Year 1 fee revenue",
                        "$1.9M",
                        "$1.9M",
                    ),
                ),
                2: (
                    _fact(
                        "F05",
                        "Expected annual volume",
                        "$120M year 3",
                        "Year 3 (2029) | $120M",
                        "case p.6 tbl 2",
                    ),
                ),
            },
        ),
        _p(
            "Fee revenue assumes an average all-in fee of roughly four percent "
            "of send value in year 1, falling slightly as volume grows. Volumes "
            "are send values in US dollars and exclude any exchange-rate margin."
        ),
    )
    page7 = _page(
        _h("7. Market and competitive context"),
        _p(
            "The corridor is served by money transfer operators, fintech apps "
            "and a small number of banks. Money transfer operators dominate "
            "cash-based sending, while fintech apps have taken share among "
            "digitally active customers by offering low fees and fast delivery "
            "to bank accounts. Few United States banks offer a direct, in-app "
            "remittance service to Mexico."
        ),
        _p(
            "Pricing in the corridor has fallen steadily. The Bank proposes a "
            "transparent fee tier by send value, with no exchange-rate margin "
            "beyond the rate disclosed in the send flow. Early testing with "
            "customers suggests that clear pricing and a trusted brand are "
            "worth more to them than being the cheapest option."
        ),
        _p(
            "The Bank's differentiators are its existing relationship with the "
            "sender, the convenience of sending from the account where pay is "
            "deposited, and the Bank's established fraud and customer "
            "protection processes. The service is not expected to attract "
            "customers who are new to the Bank in year 1."
        ),
        _p(
            "Competitive response is a risk to volume rather than to the case "
            "itself. The projections in section 6 assume modest adoption and do "
            "not rely on price leadership."
        ),
    )
    page8 = _page(
        _h("8. Regulatory and compliance context"),
        _p(
            "As a United States bank, the Bank must operate its remittance "
            "service within its anti-money laundering and counter-terrorist "
            "financing program, apply sanctions screening to every party to a "
            "transfer and meet its consumer disclosure and error-resolution "
            "obligations. This case does not seek any exemption from existing "
            "policy."
        ),
        _p(
            "Under the Bank's change governance, any new product, third-party "
            "relationship or geography must be assessed by FCRM before it goes "
            "live. This change introduces all three: a new consumer product, a "
            "new payout partner arrangement for the corridor and a new "
            "destination country, so the assessment will consider each of them."
        ),
        _p(
            "The business has engaged Compliance and Legal early. Legal is "
            "reviewing the partner agreement, and Compliance has been asked to "
            "confirm the disclosure content that will apply to the corridor. "
            "Their conclusions will be recorded in the assessment, not assumed "
            "in this document."
        ),
        _p(
            "Regulatory expectations on third-party oversight apply to the "
            "partner relationship. The Bank will keep a due diligence file for "
            "the partner, review its performance and controls at least annually "
            "and retain the right to audit, as set out in the partner agreement."
        ),
    )
    page9 = _page(
        _h("9. Key risks and mitigations"),
        _p(
            "The Payments unit has identified the risks below. They are the "
            "business's own view and are offered to help the FCRM analyst; the "
            "FCRM assessment will form its own view of inherent risk and of the "
            "strength of each control."
        ),
        _t(
            ("Risk", "Mitigation proposed"),
            [
                (
                    "Sanctions exposure on cross-border payments",
                    "Screen sender, beneficiary and partner on every transfer",
                ),
                (
                    "Structuring and layering through repeat sends",
                    "Transaction monitoring and case review by Financial Crimes",
                ),
                (
                    "Failure or misconduct by the payout partner",
                    "Contract controls, daily reporting, annual review and audit",
                ),
                (
                    "Fraud and scams targeting senders",
                    "Beneficiary confirmation and scam warnings in the app",
                ),
                (
                    "Misuse of beneficiary account details",
                    "Data minimisation, encryption and access logging",
                ),
            ],
            (230.0, 274.0),
        ),
        _p(
            "The business regards third-party concentration as the most "
            "significant of these risks, because all payouts in the corridor "
            "depend on a single partner. A contingency partner is not proposed "
            "for the first release; the Payments unit will revisit this at the "
            "post-launch review."
        ),
    )
    page10 = _page(
        _h("10. Proposed controls and monitoring"),
        _p(
            "Customer controls: only existing, fully onboarded retail customers "
            "can use the service. Each sender is screened against sanctions "
            "lists at onboarding and again whenever lists are updated, and every "
            "transfer is screened before funds leave the Bank."
        ),
        _p(
            "Transaction controls: transfers are monitored for unusual activity "
            "and alerts are reviewed by the Financial Crimes Operations team "
            "using the Bank's standard case management process."
        ),
        _p(
            "Partner controls: the partner agreement requires the partner to "
            "maintain an anti-money laundering program, to screen beneficiaries, "
            "to report exceptions daily and to give the Bank audit rights. The "
            "Payments unit will own day-to-day oversight and will report "
            "quarterly to FCRM."
        ),
        _p(
            "Post-launch review: the Bank will review the service six months "
            "after launch, covering volumes against forecast, alert outcomes, "
            "complaints and partner performance, and will report the result to "
            "the Risk Committee."
        ),
    )
    return [page6, page7, page8, page9, page10]


def _business_case_back() -> list[Page]:
    """Pages 11 to 14: plan, costs, assumptions, approvals and contacts."""
    page11 = _page(
        _h("11. Implementation plan"),
        _p(
            "The plan below is indicative. Dates after the FCRM decision depend "
            "on its outcome and on any conditions the Risk Committee attaches."
        ),
        _t(
            ("Milestone", "Target"),
            [
                ("FCRM change assessment and decision", "October 2026"),
                ("Partner agreement finalised", "October 2026"),
                ("Integration and end-to-end testing", "November to December 2026"),
                ("Operational readiness review", "January 2027"),
                ("Controlled launch to eligible customers", "After readiness sign-off"),
            ],
            (300.0, 204.0),
        ),
        _p(
            "The critical path runs through partner integration testing. The "
            "partner has committed a dedicated integration team, and the Bank "
            "has reserved test capacity in its payments platform for the period "
            "shown."
        ),
        _p(
            "A limited pilot with Bank employees, run before the controlled "
            "launch, will confirm end-to-end operation, reconciliation and "
            "customer support readiness. Findings from the pilot will be "
            "reported to FCRM before launch."
        ),
    )
    page12 = _page(
        _h("12. Costs and resourcing"),
        _p(
            "One-off costs are estimated at $3.1 million: $2.0 million for "
            "platform integration and testing, $0.6 million for compliance and "
            "legal review and $0.5 million for marketing and training. Ongoing "
            "costs are estimated at $1.4 million a year at the year 1 volume."
        ),
        _p(
            "The Payments unit will add six full-time staff: two product and "
            "operations roles, two customer support roles and two reconciliation "
            "analysts. Compliance and Financial Crimes Operations will absorb "
            "the additional alert volume within current capacity for the first "
            "six months, which the post-launch review will test."
        ),
        _p(
            "Funding is held within the Payments unit's approved investment "
            "envelope for 2026 and 2027. No additional capital is requested. "
            "The case breaks even in the second half of year 2 on the "
            "projections in section 6."
        ),
        _p(
            "Cost estimates exclude any remediation the FCRM assessment or the "
            "Risk Committee may require as a condition of approval. The business "
            "will update the estimates if conditions materially change the "
            "scope."
        ),
    )
    page13 = _page(
        _h("13. Assumptions, dependencies and open items"),
        _p(
            "The case assumes that the payout partner meets the service levels "
            "in the partner agreement, that the Bank's payments platform can "
            "support the additional volume without a platform change, and that "
            "eligible customers can be identified from existing onboarding "
            "records."
        ),
        _p(
            "Dependencies include completion of the partner agreement, "
            "completion of integration testing, Legal confirmation of the "
            "customer disclosures and FCRM approval. Each dependency has a named "
            "owner in the project plan."
        ),
        _p(
            "Open items at the date of this document: the partner's licensing "
            "evidence, the partner's attestation of its anti-money laundering "
            "program and the product sign-off reference. These will be "
            "completed during the change assessment."
        ),
        _p(
            "Questions on this document can be directed to the change owner "
            "listed in section 14."
        ),
    )
    page14 = _page(
        _h("14. Approvals and contacts"),
        _p(
            "This business case has been prepared by the Payments unit. "
            "Approvals are recorded below; the FCRM change assessment and the "
            "Risk Committee decision are outside the business unit's authority "
            "and are recorded in the FCRM workbench."
        ),
        _t(
            ("Approval", "Status"),
            [
                ("Business sponsor", "Approved"),
                ("Head of Payments Operations", "Approved"),
                ("Legal review of the partner agreement", "In progress"),
                ("Compliance review of customer disclosures", "In progress"),
            ],
            (300.0, 204.0),
        ),
        _t(
            ("Role", "Name", "Email", "Phone"),
            [
                (
                    "Change owner",
                    "Priya Nair",
                    "priya.nair@bank.example",
                    "(415) 555-0132",
                ),
                (
                    "Partner liaison",
                    "Lucia Fernandez",
                    "l.fernandez@remesasdelnorte.example",
                    "",
                ),
            ],
            (96.0, 96.0, 200.0, 112.0),
        ),
        _p(
            "Document history: version 1.0 submitted for FCRM change "
            "assessment. Later versions will be recorded in the workbench."
        ),
    )
    return [page11, page12, page13, page14]


def business_case() -> DocumentSpec:
    """The 14-page business case (scene 3)."""
    return DocumentSpec(
        filename=BUSINESS_CASE_FILE,
        title="Business case: consumer remittances to Mexico (synthetic)",
        subject="Synthetic business case for FCRM change FCRM-2026-0151",
        pages=tuple(
            _business_case_front() + _business_case_middle() + _business_case_back()
        ),
        running_notice=(
            "Business case | Consumer remittances to Mexico via payout partner "
            "| Payments | version 1.0 | SYNTHETIC"
        ),
    )


# ------------------------------------------------------------------ contract
def _contract_front() -> list[Page]:
    """Pages 1 to 5: parties, definitions, services, settlement, payout methods."""
    page1 = _page(
        _h("Payout Partner Services Agreement"),
        _p(
            "This Payout Partner Services Agreement (the Agreement) is made on "
            "18 September 2026 between the parties named below. It governs the "
            "payout of consumer remittances in Mexico on behalf of the Bank."
        ),
        _p(
            "Parties: (1) the Bank, a United States national bank acting through "
            "its Payments unit (the Bank); and (2) Remesas del Norte S.A., a "
            "sociedad anonima organised under the laws of Mexico, with its "
            "registered office in Monterrey, Nuevo Leon (the Partner).",
            _fact(
                "F06",
                "Partner or vendor identity",
                "Remesas del Norte S.A.",
                "Remesas del Norte S.A.",
                "contract p.1",
            ),
        ),
        _p(
            "Background: the Bank intends to offer consumer remittances from the "
            "United States to Mexico. The Partner provides payout services in "
            "Mexico and already supplies payment services to the Bank in the "
            "United States. The parties wish to record the terms on which the "
            "Partner will pay out the Bank's remittances."
        ),
        _p(
            "The parties agree as follows. Capitalised terms are defined in "
            "section 1. Headings are for convenience only and do not affect the "
            "meaning of this Agreement. A reference to a section is to a "
            "section of this Agreement."
        ),
    )
    page2 = _page(
        _h("§1 Definitions and interpretation"),
        _p(
            "§1.1 In this Agreement: Business Day means a day other than a "
            "Saturday, a Sunday or a public holiday in New York or in Mexico "
            "City; Corridor means payouts from the United States to Mexico; "
            "Payout means the credit of funds to a Beneficiary in accordance "
            "with a Payout Instruction; and Services means the services "
            "described in section 2."
        ),
        _p(
            "§1.2 Beneficiary means an individual in Mexico named in a Payout "
            "Instruction. Payout Instruction means an instruction from the Bank "
            "to pay a stated amount to a Beneficiary, sent through the interface "
            "agreed under section 6. Settlement Currency has the meaning given "
            "in section 4."
        ),
        _p(
            "§1.3 Applicable Law means the laws and regulations that apply to a "
            "party or to the Services, including those on anti-money laundering, "
            "counter-terrorist financing, sanctions, data protection and "
            "consumer protection. Each reference to a law includes that law as "
            "amended from time to time."
        ),
        _p(
            "§1.4 The words including and in particular do not limit the words "
            "that come before them. A reference to writing includes email. If "
            "there is a conflict between the sections of this Agreement and a "
            "schedule, the sections prevail."
        ),
    )
    page3 = _page(
        _h("§2 The Services"),
        _p(
            "§2.1 The Partner shall pay out each Payout Instruction it accepts "
            "to the Beneficiary named in it, promptly and in any event by the "
            "end of the Business Day after receipt, and shall confirm each "
            "Payout to the Bank in the form agreed."
        ),
        _p(
            "§2.2 The Partner shall not accept a Payout Instruction that is "
            "incomplete, that names a Beneficiary the Partner cannot verify, or "
            "that the Partner reasonably believes would breach Applicable Law, "
            "and shall tell the Bank promptly if it rejects one."
        ),
        _h("§3 Standards of performance"),
        _p(
            "§3.1 The Partner shall perform the Services with reasonable skill "
            "and care, using suitably qualified personnel, and in accordance "
            "with Applicable Law and the Bank's written policies that are "
            "provided to it from time to time."
        ),
        _h("§4 Settlement and fees"),
        _p(
            "§4.1 The Settlement Currency is Mexican pesos (MXN). The Bank shall "
            "fund each Payout in advance by transferring the equivalent amount "
            "to the Partner's settlement account in accordance with section 4.3.",
            _fact(
                "F23",
                "Settlement currency",
                "MXN",
                "Settlement Currency is Mexican pesos (MXN)",
                "contract p.3 §4",
            ),
        ),
    )
    page4 = _page(
        _p(
            "§4.2 The exchange rate applied to each Payout is the rate quoted to "
            "the customer at the time of the transfer. The Partner shall not "
            "apply any additional margin to that rate and shall give the Bank a "
            "daily report of rates and amounts applied."
        ),
        _p(
            "§4.3 The Bank shall maintain a prefunded balance with the Partner "
            "sufficient to cover expected Payouts for the next Business Day. The "
            "Partner shall tell the Bank promptly if the balance falls below the "
            "agreed minimum and may suspend Payouts until it is restored."
        ),
        _p(
            "§4.4 The Bank shall pay the Partner the fees set out in Schedule 2 "
            "within thirty days of receiving a correct invoice. Fees are fixed "
            "for the first twelve months and may then be reviewed once in each "
            "year by agreement."
        ),
        _p(
            "§4.5 The parties shall reconcile Payouts and settlement balances "
            "each Business Day. Any difference that is not resolved within two "
            "Business Days shall be escalated to the relationship managers named "
            "in Schedule 1."
        ),
        _p(
            "§4.6 Each party is responsible for its own taxes. Fees are "
            "exclusive of any value added or similar tax, which the Bank shall "
            "pay where it is properly due on the Services."
        ),
    )
    page5 = _page(
        _h("§5 Payout methods"),
        _p(
            "§5.1 The Partner shall pay out each remittance solely by credit to "
            "a bank account held by the Beneficiary at a Mexican bank (bank "
            "deposit). Cash pickup, mobile wallet payout and home delivery are "
            "not within the scope of the Services.",
            _fact(
                "F21",
                "Payout channels",
                "Bank deposit only",
                "solely by credit to a bank account held by the Beneficiary at a "
                "Mexican bank (bank deposit)",
                "contract p.5 (bank deposit only)",
            ),
        ),
        _p(
            "§5.2 The Partner shall verify that the account named in a Payout "
            "Instruction exists and is held in the name of the Beneficiary "
            "before it credits the funds, and shall return the funds to the Bank "
            "if verification fails."
        ),
        _p(
            "§5.3 A change to the payout methods in this section requires a "
            "written amendment signed by both parties and the Bank's prior "
            "completion of its internal change assessment."
        ),
        _h("§6 Systems and security"),
        _p(
            "§6.1 The parties shall exchange Payout Instructions, confirmations "
            "and reports through the secure interface described in Schedule 3. "
            "Each party is responsible for the security of its own systems and "
            "shall notify the other promptly of any incident that affects the "
            "Services."
        ),
    )
    return [page1, page2, page3, page4, page5]


def _contract_back() -> list[Page]:
    """Pages 6 to 9: reporting, data, AML and audit, term, law, signatures."""
    page6 = _page(
        _p(
            "§6.2 The Partner shall keep access to the interface and to "
            "Beneficiary data limited to personnel who need it for the "
            "Services, shall log that access and shall make the logs available "
            "to the Bank on request."
        ),
        _h("§7 Service levels and reporting"),
        _p(
            "§7.1 The Partner shall complete at least 99 percent of accepted "
            "Payouts by the end of the Business Day after receipt, and shall "
            "report its performance against that level to the Bank each month."
        ),
        _p(
            "§7.2 The Partner shall report exceptions to the Bank daily, "
            "including returned Payouts, rejected instructions and any Payout "
            "delayed beyond the agreed period, and shall provide a monthly "
            "summary of volumes by value band."
        ),
        _h("§8 Confidentiality and data protection"),
        _p(
            "§8.1 Each party shall keep the other's confidential information "
            "secret and use it only for the purposes of this Agreement, except "
            "where disclosure is required by law or by a regulator."
        ),
        _p(
            "§8.2 The Partner shall process personal data of customers and "
            "Beneficiaries only to provide the Services, shall protect it with "
            "appropriate technical and organisational measures and shall not "
            "transfer it outside Mexico or the United States without the Bank's "
            "written consent."
        ),
    )
    page7 = _page(
        _h("§9 Anti-money laundering, sanctions screening and audit rights"),
        _p(
            "§9.1 This section sets out the Partner's anti-money laundering, "
            "sanctions screening and audit obligations under this Agreement. "
            "They apply in addition to Applicable Law and continue for as long "
            "as the Partner holds Beneficiary data.",
            _fact(
                "F12",
                "Contract AML clauses reference",
                "contract section 9 (AML, screening, audit rights)",
                "anti-money laundering, sanctions screening and audit obligations",
                "contract p.7 §9",
            ),
        ),
        _p(
            "§9.2 The Partner shall maintain a written anti-money laundering "
            "program that is appropriate to its size and the Corridor, shall "
            "review it at least annually and shall give the Bank a copy on "
            "request."
        ),
        _p(
            "§9.3 The Partner shall screen each Beneficiary and each Payout "
            "against applicable sanctions lists before it makes a Payout, shall "
            "not make a Payout that matches a list without the Bank's written "
            "clearance and shall tell the Bank promptly of any match."
        ),
        _p(
            "§9.4 The Partner shall tell the Bank promptly of any suspicious "
            "activity connected with the Services, to the extent the law allows, "
            "and shall cooperate with the Bank's reasonable requests for "
            "information to meet the Bank's own reporting duties."
        ),
        _p(
            "§9.5 The Bank, its auditors and its regulators may audit the "
            "Partner's records, systems and premises that relate to the "
            "Services, on reasonable notice and at least once in each year. The "
            "Partner shall cooperate and shall correct any finding within the "
            "period the Bank reasonably sets."
        ),
    )
    page8 = _page(
        _p(
            "§9.6 The Partner shall keep records of each Payout, each screening "
            "result and each report made under this section for at least five "
            "years after the Payout, and shall make them available to the Bank "
            "or a regulator on request."
        ),
        _h("§10 Term and termination"),
        _p(
            "§10.1 This Agreement starts on the date it is signed and continues "
            "for three years unless it is ended earlier under this section. It "
            "renews for successive periods of one year unless a party gives "
            "ninety days' notice not to renew."
        ),
        _p(
            "§10.2 Either party may end this Agreement immediately by written "
            "notice if the other commits a material breach that it fails to "
            "remedy within thirty days of notice, or becomes insolvent."
        ),
        _p(
            "§10.3 The Bank may suspend Payouts or end this Agreement "
            "immediately if the Partner breaches section 9, if a regulator so "
            "requires, or if the Bank reasonably believes that continuing would "
            "breach Applicable Law."
        ),
        _h("§11 Liability"),
        _p(
            "§11.1 Nothing in this Agreement limits liability that cannot be "
            "limited by law. Subject to that, each party's total liability to "
            "the other in any year is limited to the fees paid or payable in "
            "that year, except for the Partner's liability for funds it has "
            "received and not paid out."
        ),
    )
    page9 = _page(
        _h("§12 Governing law and disputes"),
        _p(
            "§12.1 This Agreement is governed by the laws of the State of New "
            "York. The parties shall first try to resolve any dispute through "
            "their relationship managers and then through senior management "
            "before starting proceedings."
        ),
        _h("§13 General"),
        _p(
            "§13.1 This Agreement, with its schedules, is the whole agreement "
            "between the parties on its subject. A change to it must be in "
            "writing and signed by both parties. A party may not assign it "
            "without the other's written consent."
        ),
        _p("Signed for and on behalf of the Bank"),
        _p("Name: Elena Whitfield"),
        _p("Title: Chief Compliance Officer"),
        _p("Signed for and on behalf of the Partner"),
        _p("Name: Alejandro Reyes Camacho"),
        _p("Title: Chief Executive Officer"),
    )
    return [page6, page7, page8, page9]


def payout_partner_contract() -> DocumentSpec:
    """The 9-page payout partner contract (scene 3)."""
    return DocumentSpec(
        filename=CONTRACT_FILE,
        title="Payout Partner Services Agreement (synthetic)",
        subject="Synthetic contract between the Bank and Remesas del Norte S.A.",
        pages=tuple(_contract_front() + _contract_back()),
        running_notice=(
            "Payout Partner Services Agreement | Remesas del Norte S.A. "
            "| Execution version | SYNTHETIC"
        ),
    )


# -------------------------------------------------------------- scope letter
def vendor_dd_scope_letter() -> DocumentSpec:
    """The 2-page due-diligence scope letter the PO attaches in scene 11.

    Two pages is below the extractor's three-page minimum for stripping a
    running header and footer, so this document has none.
    """
    page1 = _page(
        _h("Brightwater Assurance LLP, Financial Services Advisory"),
        _p("Date: 24 September 2026"),
        _p("To: Priya Nair, Payments, for the FCRM change desk"),
        _p(
            "Subject: Scope of the corridor-specific due diligence review of "
            "Remesas del Norte S.A., US to Mexico corridor. This letter is a "
            "synthetic demonstration document."
        ),
        _h("1. Engagement and timing"),
        _p(
            "Brightwater Assurance LLP has been engaged by the Payments unit to "
            "carry out a corridor-specific due diligence review of Remesas del "
            "Norte S.A. Fieldwork is scheduled for 2 to 20 November 2026, with "
            "the final report due by 18 December 2026.",
            _fact(
                "F11",
                "Partner AML programme attestation",
                "Corridor review scheduled Nov 2026",
                "Fieldwork is scheduled for 2 to 20 November 2026",
                stage=STAGE_CLARIFICATION,
            ),
        ),
        _h("2. Scope of the review"),
        _p(
            "The review covers the partner's corridor operations for payouts "
            "from the United States to Mexico, including the payout agent "
            "network. The network comprises 38 cash-pickup agent locations in 6 "
            "states, and the review will test the controls over those "
            "locations.",
            _fact(
                None,
                "Payout agent network",
                "38 cash-pickup agent locations in 6 states",
                "38 cash-pickup agent locations in 6 states",
                stage=STAGE_CLARIFICATION,
            ),
        ),
        _p(
            "The volume basis for sampling is the Payments unit's forecast of "
            "about 62,000 transactions a month by month 6 of the service, which "
            "the reviewer will use to size testing.",
            _fact(
                "F32",
                "Expected monthly transaction count",
                "About 62,000 per month by month 6",
                "about 62,000 transactions a month by month 6",
                stage=STAGE_CLARIFICATION,
            ),
        ),
    )
    page2 = _page(
        _h("3. Matters outside the scope"),
        _p(
            "The review does not cover the agents' own customer files. Agents "
            "are onboarded and monitored under the Partner's KYC standard v4, "
            "and sampling of agent-level KYC files is outside this engagement.",
            _fact(
                None,
                "Partner KYC standard",
                "v4",
                "Partner's KYC standard v4",
                stage=STAGE_CLARIFICATION,
            ),
        ),
        _h("4. Deliverables and timeline"),
        _p(
            "We will issue a written report with findings rated by severity, a "
            "management response template and, where we identify gaps, "
            "recommended remediation with owners and dates. A draft will be "
            "shared with the Payments unit and FCRM before the final report."
        ),
        _h("5. Assumptions and fees"),
        _p(
            "The estimate assumes access to the partner's premises, records and "
            "personnel in Monterrey and at a sample of agent locations. Our fee "
            "is a fixed amount agreed separately with the Payments unit."
        ),
        _p("Yours sincerely,"),
        _p("Brightwater Assurance LLP, Financial Services Advisory"),
    )
    return DocumentSpec(
        filename=SCOPE_LETTER_FILE,
        title="Vendor due diligence scope letter (synthetic)",
        subject="Synthetic scope letter for the Remesas del Norte corridor review",
        pages=(page1, page2),
        running_notice=None,
    )


def document_specs() -> list[DocumentSpec]:
    """The three demo documents, in the order the demo uploads them."""
    return [business_case(), payout_partner_contract(), vendor_dd_scope_letter()]
