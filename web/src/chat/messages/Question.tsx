import { MoreHorizontalIcon } from "lucide-react";
import { type SyntheticEvent, useState } from "react";

import { Message, MessageAttachments, MessageContent } from "@/components/ai-elements/message";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { type UploadCard, type UserMessage, useRemoveUpload } from "../api";
import { DocumentCard } from "../DocumentCard";
import { usePdfPanel } from "../pdf-panel";
import { docName } from "./format";

/** A question: its document cards above a right-aligned bubble. */
export function Question({ message }: { message: UserMessage }) {
  return (
    <Message from="user">
      {message.attachments.length > 0 && (
        <MessageAttachments>
          {message.attachments.map((card) => (
            <SentCard key={card.id} card={card} />
          ))}
        </MessageAttachments>
      )}
      <MessageContent>
        <p className="whitespace-pre-wrap">{message.text}</p>
      </MessageContent>
    </Message>
  );
}

// The card is clickable; events from the menu (including its portal) stop at the menu.
const stop = (e: SyntheticEvent) => e.stopPropagation();

/** A sent card: opens in the PDF panel; its menu has Open and, for uploads, Remove from library. */
function SentCard({ card }: { card: UploadCard }) {
  const pdf = usePdfPanel();
  const removeUpload = useRemoveUpload();
  const [confirming, setConfirming] = useState(false);
  const open = () =>
    pdf.open({ docId: card.doc_id, filename: card.filename, jobNumber: card.job_number, project: card.project, page: 1 });

  const menu = (
    <div onClick={stop} onKeyDown={stop}>
      <DropdownMenu modal={false}>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon" className="size-6 shrink-0" aria-label={`${card.filename} options`}>
            <MoreHorizontalIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={open}>Open</DropdownMenuItem>
          {card.removable && !card.removed && (
            <DropdownMenuItem variant="destructive" onSelect={() => setConfirming(true)}>
              Remove from library
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );

  return (
    <>
      <DocumentCard card={card} onOpen={open} menu={menu} />
      <AlertDialog open={confirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {docName(card.filename)} from the library?</AlertDialogTitle>
            <AlertDialogDescription>It will no longer be searched in any chat.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={() => removeUpload.mutate(card.id)}>
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}
