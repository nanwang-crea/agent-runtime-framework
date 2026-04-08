import { MarkdownContent } from "../MarkdownContent";

type MessageBubbleProps = {
  role: string;
  content: string;
  isStreaming?: boolean;
};

export function MessageBubble({ role, content, isStreaming = false }: MessageBubbleProps) {
  return (
    <div className={`message-bubble ${role} ${isStreaming ? "streaming" : ""}`}>
      <span className="message-role">{role === "user" ? "You" : "Assistant"}</span>
      <div className="message-content">
        {role === "assistant" ? <MarkdownContent content={content} /> : <div>{content}</div>}
      </div>
    </div>
  );
}
