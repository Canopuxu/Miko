import { useState, type ReactNode } from 'react';
import * as AlertDialog from '@radix-ui/react-alert-dialog';
import { AlertCircle, LoaderCircle } from 'lucide-react';

export function useNotice() {
  const [notice,setText]=useState('');
  const [failed,setFailed]=useState(false);
  function setNotice(text:string) {setText(text);setFailed(false);}
  function setFailure(cause:unknown) {setText(String(cause));setFailed(true);}
  return {notice,setNotice,setFailure,failed};
}

export function EmptyState({title, children, loading = false}: {
  title: string; children?: ReactNode; loading?: boolean;
}) {
  return <div className="empty-state" role={loading ? 'status' : undefined}>
    {loading && <LoaderCircle className="spinning" size={20}/>}
    <h2>{title}</h2>{children && <div className="empty-description">{children}</div>}
  </div>;
}

export function useDiscardGuard(dirty: boolean, discard: () => void = () => {}) {
  const [pending, setPending] = useState<(() => void) | null>(null);
  function proceed(action: () => void) {
    if (dirty) setPending(() => action); else action();
  }
  const dialog = <AlertDialog.Root open={!!pending} onOpenChange={open => {if (!open) setPending(null);}}>
    <AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay"/>
      <AlertDialog.Content className="confirm-dialog">
        <span className="dialog-symbol"><AlertCircle size={24}/></span>
        <AlertDialog.Title>保留这次编辑？</AlertDialog.Title>
        <AlertDialog.Description>有尚未保存的修改。你可以继续编辑，或放弃这些修改再进行操作。</AlertDialog.Description>
        <div className="dialog-actions"><AlertDialog.Cancel className="ui-button">继续编辑</AlertDialog.Cancel>
          <AlertDialog.Action className="ui-button warning" onClick={() => {discard(); pending?.(); setPending(null);}}>
            放弃修改并继续</AlertDialog.Action></div>
      </AlertDialog.Content></AlertDialog.Portal>
  </AlertDialog.Root>;
  return {proceed, dialog};
}
