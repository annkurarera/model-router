export interface Example { level: "Easy" | "Hard"; prompt: string }

export const PAIRS: { topic: string; easy: string; hard: string }[] = [
  {
    topic: "Customer complaint",
    easy: "Tag this complaint with one intent: my card was charged twice for one purchase.",
    hard: "A customer writes: my card was charged twice, the card is now blocked and last month's refund never arrived. Which issue should the bank handle first, and why?",
  },
  {
    topic: "Loan maths",
    easy: "What is the EMI on a Rs 10 lakh loan at 10% a year for 5 years?",
    hard: "Home loan of Rs 40 lakh at 8.75% for 20 years. After 3 years I prepay Rs 5 lakh and keep the same EMI. How many months sooner does the loan end?",
  },
  {
    topic: "Loan documents",
    easy: "Extract the borrower name and loan amount: Dear Priya Sharma, we sanction a home loan of Rs 25,00,000.",
    hard: "Extract borrower name, PAN, co-applicant, loan amount, rate, tenure, sanction date and processing fee as JSON from: Ref HL/2026/118. Sanctioned 12-Oct-26 to Ms Priya Sharma (PAN ABCPS1234K), co-applicant Rahul Sharma: Rs 25 lakh at 8.75% p.a. for 240 months; fee 0.5% + GST.",
  },
  {
    topic: "SQL",
    easy: "Write SQL for total card spend by merchant category.",
    hard: "Write SQL for the top 3 merchants in each city by card spend, with each merchant's share of its city's total. Handle ties.",
  },
];

/** Easy and hard versions of the same kind of question, so visitors see the router choose differently. */
export default function ExamplePairs({ onPick }: { onPick: (e: Example) => void }) {
  return (
    <section className="pairs" aria-labelledby="pairs-title">
      <h2 id="pairs-title">Try an easy and a hard question</h2>
      <ul>
        {PAIRS.map((p) => (
          <li key={p.topic} className="pair">
            <span className="pair-topic">{p.topic}</span>
            <button type="button" className="pair-btn" onClick={() => onPick({ level: "Easy", prompt: p.easy })}>
              <span className="pair-level easy">Easy</span>
              <span className="pair-text">{p.easy}</span>
            </button>
            <button type="button" className="pair-btn" onClick={() => onPick({ level: "Hard", prompt: p.hard })}>
              <span className="pair-level hard">Hard</span>
              <span className="pair-text">{p.hard}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
