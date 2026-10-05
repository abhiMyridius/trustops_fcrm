import { Icon } from '@/components/ui/Icon';

function humanTitle(name: string): string {
  return name
    .replace(/\.(pdf|docx|xlsx|pptx)$/i, '')
    .replace(/_v\d+$/i, '')
    .replace(/[_-]+/g, ' ')
    .replace(/\bv\d+\b/gi, '')
    .trim();
}

type DocKind = 'contract' | 'questionnaire' | 'audit' | 'diagram' | 'memo';

function docKind(name: string): DocKind {
  const n = name.toLowerCase();
  if (/msa|agreement|contract/.test(n)) return 'contract';
  if (/duediligence|questionnaire/.test(n)) return 'questionnaire';
  if (/soc\s?2|audit/.test(n)) return 'audit';
  if (/diagram|workflow/.test(n)) return 'diagram';
  return 'memo';
}

const Sheet = ({ children }: { children: React.ReactNode }) => (
  <div className="bg-white border border-border-strong rounded-md shadow-sm mx-auto p-8 text-[12px] leading-relaxed text-[#1a1a1a]" style={{ width: '100%', maxWidth: 520, fontFamily: "'Segoe UI', sans-serif" }}>
    {children}
  </div>
);

const Letterhead = () => (
  <div className="flex items-center justify-between border-b-2 border-carbon-moss pb-3 mb-6">
    <div className="flex items-center gap-2">
      <div className="w-6 h-6 rounded-[4px] bg-carbon-moss flex items-center justify-center text-quantum-leaf font-bold text-[11px]">M</div>
      <span className="text-[11px] font-bold text-carbon-moss tracking-wide">MYRIDIUS NATIONAL BANK</span>
    </div>
    <span className="text-[9px] font-semibold uppercase tracking-wider text-text-faint">Confidential</span>
  </div>
);

function ContractPreview({ title }: { title: string }) {
  return (
    <Sheet>
      <Letterhead />
      <p className="text-center text-[13px] font-bold tracking-wide uppercase mb-1">Master Services Agreement</p>
      <p className="text-center text-[11px] text-text-muted mb-6">{title}</p>
      <p className="mb-4 text-[11.5px]">
        This Agreement ("<strong>Agreement</strong>") is entered into by and between Myridius National Bank ("<strong>Company</strong>") and the counterparty
        identified on the signature page hereto ("<strong>Vendor</strong>"), collectively the "<strong>Parties</strong>."
      </p>
      {[
        { h: '1. Scope of Services', b: 'Vendor shall provide the services described in Schedule A, in accordance with the service levels set out therein.' },
        { h: '2. Data Protection & Confidentiality', b: 'Vendor shall process Company data solely as necessary to perform the Services and in accordance with the data-handling terms set out in Schedule B.' },
        { h: '3. Term & Termination', b: 'This Agreement commences on the Effective Date and continues until terminated by either Party in accordance with Section 8.' },
        { h: '4. Compliance with Applicable Law', b: 'Vendor represents that it will comply with all applicable laws and regulations relevant to the Services, including financial crime and data-protection requirements.' },
      ].map((c) => (
        <div key={c.h} className="mb-3">
          <p className="font-semibold text-[11.5px] mb-0.5">{c.h}</p>
          <p className="text-[11px] text-[#333]">{c.b}</p>
        </div>
      ))}
      <div className="grid grid-cols-2 gap-8 mt-8 pt-4 border-t border-border-hairline">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-faint mb-4">For Company</p>
          <div className="border-b border-[#999] h-6 mb-1" />
          <p className="text-[10px] text-text-faint">Signature</p>
        </div>
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-wide text-text-faint mb-4">For Vendor</p>
          <div className="border-b border-[#999] h-6 mb-1" />
          <p className="text-[10px] text-text-faint">Signature</p>
        </div>
      </div>
    </Sheet>
  );
}

function QuestionnairePreview({ title }: { title: string }) {
  const qa = [
    { q: 'Legal entity name and jurisdiction of incorporation', a: 'See Section 1 - entity details as registered with local authorities.' },
    { q: 'Does your organization maintain a documented AML/BSA compliance program?', a: 'Yes - reviewed annually, last update on file.' },
    { q: 'Data retention and destruction policy', a: 'Retention schedule enclosed in Appendix C.' },
    { q: 'Sub-processors or fourth parties used in delivering this service', a: 'Two sub-processors disclosed - see Appendix D.' },
    { q: 'Most recent independent security audit or certification', a: 'SOC 2 Type II, renewed annually.' },
    { q: 'Business continuity / disaster recovery plan in place?', a: 'Yes - tested semi-annually, summary attached.' },
  ];
  return (
    <Sheet>
      <Letterhead />
      <p className="text-center text-[13px] font-bold tracking-wide uppercase mb-1">Vendor Due Diligence Questionnaire</p>
      <p className="text-center text-[11px] text-text-muted mb-6">{title}</p>
      <div className="flex flex-col gap-3">
        {qa.map((item, i) => (
          <div key={i} className="border-b border-border-hairline pb-2.5">
            <p className="text-[11.5px] font-semibold mb-1">
              {i + 1}. {item.q}
            </p>
            <p className="text-[11px] text-[#333] pl-3.5">{item.a}</p>
          </div>
        ))}
      </div>
    </Sheet>
  );
}

function AuditPreview({ title }: { title: string }) {
  const criteria = ['Security', 'Availability', 'Confidentiality', 'Processing Integrity'];
  return (
    <Sheet>
      <Letterhead />
      <p className="text-center text-[13px] font-bold tracking-wide uppercase mb-1">Independent Service Auditor's Report</p>
      <p className="text-center text-[11px] text-text-muted mb-6">{title}</p>
      <p className="text-[11.5px] mb-4">
        We have examined the accompanying description of the vendor's system and the suitability of the design and operating effectiveness of controls
        relevant to the applicable Trust Services Criteria throughout the review period.
      </p>
      <table className="w-full text-[11px] mb-4 border border-border-hairline">
        <thead>
          <tr className="bg-surface-tertiary text-left">
            <th className="px-2.5 py-1.5 font-semibold">Trust Services Criteria</th>
            <th className="px-2.5 py-1.5 font-semibold">Result</th>
          </tr>
        </thead>
        <tbody>
          {criteria.map((c) => (
            <tr key={c} className="border-t border-border-hairline">
              <td className="px-2.5 py-1.5">{c}</td>
              <td className="px-2.5 py-1.5 text-teal font-semibold">No exceptions noted</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-[#333]">
        In our opinion, the controls tested were suitably designed and operating effectively to provide reasonable assurance the criteria were achieved
        throughout the review period.
      </p>
    </Sheet>
  );
}

function DiagramPreview({ title }: { title: string }) {
  const steps = ['Customer', 'Origination System', 'Vendor / Partner', 'Response & Settlement'];
  return (
    <Sheet>
      <Letterhead />
      <p className="text-center text-[13px] font-bold tracking-wide uppercase mb-1">Process / Data-Flow Diagram</p>
      <p className="text-center text-[11px] text-text-muted mb-8">{title}</p>
      <div className="flex items-center justify-center gap-1.5 flex-wrap py-6">
        {steps.map((s, i) => (
          <div key={s} className="flex items-center gap-1.5">
            <div className="border-2 border-carbon-moss rounded-md px-3 py-3 text-[10.5px] font-semibold text-carbon-moss text-center w-[92px]">{s}</div>
            {i < steps.length - 1 && <Icon name="arrow-right" size={14} className="text-text-faint shrink-0" />}
          </div>
        ))}
      </div>
      <p className="text-[10.5px] text-text-faint text-center mt-4">Simplified representation - see source diagram for full detail.</p>
    </Sheet>
  );
}

function MemoPreview({ title }: { title: string }) {
  return (
    <Sheet>
      <Letterhead />
      <p className="text-center text-[13px] font-bold tracking-wide uppercase mb-4">Memorandum</p>
      <div className="grid grid-cols-[50px_1fr] gap-y-1 text-[11px] mb-5">
        <span className="font-semibold text-text-faint">TO:</span>
        <span>FCRM Review File</span>
        <span className="font-semibold text-text-faint">FROM:</span>
        <span>Product &amp; Business Sponsor</span>
        <span className="font-semibold text-text-faint">RE:</span>
        <span>{title}</span>
      </div>
      <p className="text-[11.5px] mb-3">
        This memo outlines the business rationale, target customers, and operational considerations for the proposed change described above. It has been
        prepared to support the risk assessment intake and reflects the information available at the time of drafting.
      </p>
      <p className="text-[11.5px] mb-3">
        Key stakeholders have reviewed the scope and confirmed alignment with existing controls and reporting lines. Open items are being tracked through
        the standard clarification process with FCRM.
      </p>
      <p className="text-[11.5px]">Please direct questions on this memo to the business sponsor named on the case record.</p>
    </Sheet>
  );
}

export function DocumentPreview({ name }: { name: string }) {
  const title = humanTitle(name);
  const kind = docKind(name);
  if (kind === 'contract') return <ContractPreview title={title} />;
  if (kind === 'questionnaire') return <QuestionnairePreview title={title} />;
  if (kind === 'audit') return <AuditPreview title={title} />;
  if (kind === 'diagram') return <DiagramPreview title={title} />;
  return <MemoPreview title={title} />;
}
