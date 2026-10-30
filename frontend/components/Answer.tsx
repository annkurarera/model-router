import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/** Model answers arrive as Markdown; show headings, lists, tables and code as formatted text. */
export default function Answer({ text }: { text: string }) {
  return (
    <div className="md" data-testid="answer">
      <ReactMarkdown remarkPlugins={[remarkGfm]}
        components={{ a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" /> }}>
        {text}
      </ReactMarkdown>
    </div>
  );
}
