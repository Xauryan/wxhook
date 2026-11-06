import fs from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const dictionaryPath = path.join(root, 'scripts/locale-zh-CN.json')
const dictionary = fs.existsSync(dictionaryPath) ? JSON.parse(fs.readFileSync(dictionaryPath, 'utf8')) : {}
const audit = process.argv.includes('--audit')
const apply = process.argv.includes('--apply')
const includeTests = process.argv.includes('--tests')
const findings = new Map()
const uiProperties = ['title','label','description','desc','message','plan','confirmText','cancelBtnText','placeholder','searchPlaceholder','loading','success','error']
let changed = 0
function walk(directory) {
  for (const entry of fs.readdirSync(directory, { withFileTypes: true })) {
    const filename = path.join(directory, entry.name)
    if (entry.isDirectory()) { if (!['assets', 'test-utils'].includes(entry.name)) walk(filename); continue }
    if (!/\.tsx?$/.test(filename) || /\.gen\./.test(filename) || (!includeTests && /\.test\./.test(filename))) continue
    const original = fs.readFileSync(filename, 'utf8')
    const source = ts.createSourceFile(filename, original, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
    const edits = []
    function visit(node) {
      let value
      let visible = false
      if (ts.isJsxText(node)) {
        value = node.text.replace(/\s+/g, ' ').trim()
        visible = true
      } else if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
        value = node.text
        const parent = node.parent
        visible = ts.isJsxAttribute(parent) && ['title','placeholder','aria-label','alt','heading','label','description','desc','confirmText','cancelBtnText','resetAriaLabel'].includes(parent.name.getText(source))
          || ts.isPropertyAssignment(parent) && uiProperties.includes(parent.name.getText(source))
      }
      if (value && /[a-zA-Z]{2}/.test(value) && !/[\u3400-\u9fff]/.test(value) && visible) {
        if (!findings.has(value)) findings.set(value, path.relative(root, filename).replaceAll('\\', '/'))
      }
      // Exact literals only: route ids, enum values, CSS and runtime chat data
      // never pass through a DOM translation layer.
      const translated = value && (dictionary[value] || dictionary[value.replace(/\s+/g,' ').trim()])
      const parent = node.parent || node
      const technical = ts.isImportDeclaration(parent) || ts.isLiteralTypeNode(parent) || ts.isCaseClause(parent)
        || ts.isArrayLiteralExpression(parent) || ts.isElementAccessExpression(parent)
        || ts.isBinaryExpression(parent) && parent.operatorToken.kind !== ts.SyntaxKind.EqualsToken
        || ts.isPropertyAssignment(parent) && !uiProperties.includes(parent.name.getText(source)) && !(/\.test\./.test(filename) && !['key','code'].includes(parent.name.getText(source)))
        || ts.isCallExpression(parent) && /\.(press|pressSequentially|type|typeText)$/.test(parent.expression.getText(source))
      const literalContext = (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && !technical
      if (translated && (visible || literalContext)) {
        const replacement = ts.isJsxText(node)
          ? (node.text.match(/^\s*/)?.[0] || '') + translated + (node.text.match(/\s*$/)?.[0] || '')
          : JSON.stringify(translated)
        edits.push({ start: node.getStart(source, false), end: node.end, replacement })
        if (ts.isJsxText(node)) edits[edits.length - 1].start = node.pos
      }
      ts.forEachChild(node, visit)
    }
    visit(source)
    if (apply && edits.length) {
      let updated = original
      for (const edit of edits.sort((a,b) => b.start - a.start)) updated = updated.slice(0, edit.start) + edit.replacement + updated.slice(edit.end)
      if (fs.readFileSync(filename, 'utf8') !== original) throw new Error(`Concurrent change: ${filename}`)
      fs.writeFileSync(filename, updated)
      changed++
    }
  }
}
walk(path.join(root, 'src'))
if (audit) for (const [text, file] of findings) console.log(JSON.stringify({ text, file }))
if (apply) console.log(`Localized ${changed} files.`)
