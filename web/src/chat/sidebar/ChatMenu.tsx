import { PencilIcon, Trash2Icon } from "lucide-react";
import { type FormEvent, type ReactElement, useState } from "react";
import { toast } from "sonner";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";

import { useDeleteChat, useRenameChat } from "../api";
import { navigateToChat, useChatId } from "../route";

export interface ChatMenuProps {
  chat: { id: string; title: string | null };
  /** The menu's trigger button. */
  trigger: ReactElement;
  align?: "start" | "end";
}

/** A chat's menu: Rename and Delete, each with its dialog. */
export function ChatMenu({ chat, trigger, align = "start" }: ChatMenuProps) {
  const [dialog, setDialog] = useState<"rename" | "delete" | null>(null);
  const onOpenChange = (open: boolean) => !open && setDialog(null);
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
        <DropdownMenuContent align={align} className="w-36">
          <DropdownMenuItem onSelect={() => setDialog("rename")}>
            <PencilIcon />
            Rename
          </DropdownMenuItem>
          <DropdownMenuItem variant="destructive" onSelect={() => setDialog("delete")}>
            <Trash2Icon />
            Delete
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      <RenameDialog chat={chat} open={dialog === "rename"} onOpenChange={onOpenChange} />
      <DeleteDialog chat={chat} open={dialog === "delete"} onOpenChange={onOpenChange} />
    </>
  );
}

interface DialogProps {
  chat: ChatMenuProps["chat"];
  open: boolean;
  onOpenChange(open: boolean): void;
}

function RenameDialog({ chat, open, onOpenChange }: DialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby={undefined} showCloseButton={false}>
        {open && <RenameForm chat={chat} onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function RenameForm({ chat, onDone }: { chat: DialogProps["chat"]; onDone(): void }) {
  const [title, setTitle] = useState(chat.title ?? "");
  const rename = useRenameChat();
  const trimmed = title.trim();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!trimmed || rename.isPending) return;
    if (trimmed === chat.title) return onDone();
    rename.mutate(
      { chatId: chat.id, title: trimmed },
      { onSuccess: onDone, onError: (error) => toast.error(error.message) },
    );
  };

  return (
    <form onSubmit={submit} className="grid gap-4">
      <DialogHeader>
        <DialogTitle>Rename chat</DialogTitle>
      </DialogHeader>
      <Input
        aria-label="Chat title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onFocus={(e) => e.target.select()}
        autoFocus
      />
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" disabled={!trimmed || rename.isPending}>
          Save
        </Button>
      </DialogFooter>
    </form>
  );
}

function DeleteDialog({ chat, open, onOpenChange }: DialogProps) {
  const openChatId = useChatId();
  const remove = useDeleteChat();

  const confirm = () =>
    remove.mutate(chat.id, {
      onSuccess: () => {
        if (openChatId === chat.id) navigateToChat(null, { replace: true });
      },
      onError: (error) => toast.error(error.message),
    });

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent aria-describedby={undefined}>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete “{chat.title ?? "New chat"}”?</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction variant="destructive" onClick={confirm}>
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
