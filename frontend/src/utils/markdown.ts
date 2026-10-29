import MarkdownIt from 'markdown-it'

// html:false —— 不解析原始 HTML，避免模型输出被当作标签注入；
// breaks:true —— 单换行即换行，更贴近客服话术的书写习惯。
const md = new MarkdownIt({ html: false, linkify: true, breaks: true })

const defaultLinkOpen =
  md.renderer.rules.link_open ??
  ((tokens, index, options, _env, self) => self.renderToken(tokens, index, options))

md.renderer.rules.link_open = (tokens, index, options, _env, self) => {
  tokens[index].attrSet('target', '_blank')
  tokens[index].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, index, options, _env, self)
}

export function renderMarkdown(text: string): string {
  return md.render(text || '')
}