import ReactMarkdown from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

type MessageContentProps = {
  content: string;
  role: "user" | "assistant";
};

export function MessageContent({ content, role }: MessageContentProps) {
  if (role === "user") {
    return <div className="message-content message-content-plain">{content}</div>;
  }

  return (
    <div className="message-content message-content-markdown">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
