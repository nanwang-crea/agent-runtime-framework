import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/github-dark.css";

type MarkdownContentProps = {
  content: string;
};

export function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <div className="markdown-content">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          code({ className, children, ...props }) {
            const lang = (className || "").replace("language-", "");
            const isBlock = !props.ref;
            if (!isBlock) {
              return <code className={className} {...props}>{children}</code>;
            }
            if (lang === "diff" || lang === "patch") {
              const lines = String(children).split("\n");
              return (
                <pre className="diff-block">
                  <code>
                    {lines.map((line, i) => {
                      const cls =
                        line.startsWith("+") && !line.startsWith("+++")
                          ? "diff-add"
                          : line.startsWith("-") && !line.startsWith("---")
                          ? "diff-del"
                          : line.startsWith("@@")
                          ? "diff-hunk"
                          : "diff-ctx";
                      return (
                        <span key={i} className={cls}>
                          {line}\n
                        </span>
                      );
                    })}
                  </code>
                </pre>
              );
            }
            return (
              <pre>
                <code className={className} {...props}>
                  {children}
                </code>
              </pre>
            );
          },
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
