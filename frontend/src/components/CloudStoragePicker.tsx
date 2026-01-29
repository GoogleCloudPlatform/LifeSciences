/**
 * Copyright 2026 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import React, { useState, useEffect, useRef } from 'react';
import {
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  Button,
  ImageList,
  ImageListItem,
  ImageListItemBar,
  IconButton,
  Typography,
  Box,
  Skeleton,
} from '@mui/material';
import {
  CheckCircle as CheckCircleIcon,
  Delete as DeleteIcon,
  CloudUpload as CloudUploadIcon,
} from '@mui/icons-material';
import { StorageItem } from '../types';
import {
  getStorageFiles,
  uploadStorageFile,
  deleteStorageFile,
} from '../api/client';

interface CloudStoragePickerProps {
  open: boolean;
  onClose: () => void;
  onSelect: (item: StorageItem) => void;
}

const CloudStoragePicker: React.FC<CloudStoragePickerProps> = ({
  open,
  onClose,
  onSelect,
}) => {
  const [items, setItems] = useState<StorageItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) {
      loadFiles();
    }
  }, [open]);

  const loadFiles = async () => {
    setLoading(true);
    setError(null);
    try {
      const files = await getStorageFiles();
      setItems(files);
    } catch (err) {
      console.error('Failed to load storage files:', err);
      setError('Failed to load files from storage.');
    } finally {
      setLoading(false);
    }
  };

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = async (
    event: React.ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      setLoading(true); // Show loading while uploading/refreshing
      await uploadStorageFile(file);
      await loadFiles(); // Refresh list
    } catch (err) {
      console.error('Upload failed:', err);
      setError('Failed to upload file.');
      setLoading(false);
    } finally {
      // Reset input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleDelete = async (item: StorageItem, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent selection when clicking delete
    if (!window.confirm(`Are you sure you want to delete ${item.name}?`)) {
      return;
    }

    try {
      // The item.name usually contains the filename, but we need the full path if stored that way.
      // Our API returns name as just filename, but URI/URL might help.
      // Actually api/routes/storage.py line 58 sends name=blob.name.split('/')[-1].
      // But delete needs the full path.
      // Wait, api/routes/storage.py DELETE uses file_path which expects the blob name.
      // We need to reconstruct the path or pass it.
      // The current getStorageFiles implementation in python constructs the name stripping the prefix.
      // Let's look at the python code again:
      // name=blob.name.split('/')[-1]
      // We need to pass "dev/" + item.name or similar.
      // Ideally, the backend should return the full path ID.
      // Let's assume for now it's in the default folder.
      // Actually, looking at the python code, the `proxy_url` contains `file/{blob.name}`.
      // So we can extract it from there or just guess `dev/${item.name}` if `dev` is hardcoded.
      // Better yet, let's extract it from the proxy URL which is reliably constructed by the backend.
      // url: /api/v1/storage/file/dev/image.jpg
      const pathPart = item.url.split('/api/v1/storage/file/')[1];

      await deleteStorageFile(pathPart);
      // Optimistic update or reload
      setItems(items.filter((i) => i !== item));
    } catch (err) {
      console.error('Delete failed:', err);
      alert('Failed to delete file.');
    }
  };

  // Component to handle individual media item loading state
  const MediaItem = ({
    item,
    onSelect,
  }: {
    item: StorageItem;
    onSelect: (item: StorageItem) => void;
  }) => {
    const [mediaLoaded, setMediaLoaded] = useState(false);
    const isVideo = item.content_type.startsWith('video/');

    return (
      <ImageListItem sx={{ cursor: 'pointer' }} onClick={() => onSelect(item)}>
        {!mediaLoaded && (
          <Skeleton
            variant="rectangular"
            width="100%"
            height="100%"
            sx={{ position: 'absolute', top: 0, left: 0 }}
          />
        )}
        {isVideo ? (
          <Box
            sx={{
              height: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              bgcolor: 'grey.200',
              opacity: mediaLoaded ? 1 : 0,
            }}
            onLoad={() => setMediaLoaded(true)}
          >
            <Typography variant="caption" color="text.secondary">
              VIDEO
            </Typography>
          </Box>
        ) : (
          <img
            src={`${item.url}`}
            srcSet={`${item.url}`}
            alt={item.name}
            loading="lazy"
            style={{
              height: '100%',
              objectFit: 'cover',
              opacity: mediaLoaded ? 1 : 0,
            }}
            onLoad={() => setMediaLoaded(true)}
          />
        )}
        <ImageListItemBar
          title={item.name}
          subtitle={new Date(item.created).toLocaleDateString()}
          actionIcon={
            <Box sx={{ display: 'flex' }}>
              <IconButton
                sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                aria-label={`delete ${item.name}`}
                onClick={(e) => handleDelete(item, e)}
              >
                <DeleteIcon />
              </IconButton>
              <IconButton
                sx={{ color: 'rgba(255, 255, 255, 0.54)' }}
                aria-label={`select ${item.name}`}
              >
                <CheckCircleIcon />
              </IconButton>
            </Box>
          }
        />
      </ImageListItem>
    );
  };

  return (
    <Dialog open={open} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle
        sx={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}
      >
        Select from Cloud Storage
        <Button
          variant="contained"
          startIcon={<CloudUploadIcon />}
          onClick={handleUploadClick}
          disabled={loading}
        >
          Upload Media
        </Button>
        <input
          type="file"
          ref={fileInputRef}
          style={{ display: 'none' }}
          accept="image/*,video/*"
          onChange={handleFileChange}
        />
      </DialogTitle>
      <DialogContent dividers>
        {loading && items.length === 0 ? (
          <ImageList
            sx={{ width: '100%', height: 450 }}
            cols={3}
            rowHeight={200}
          >
            {[1, 2, 3, 4, 5, 6].map((n) => (
              <ImageListItem key={n}>
                <Skeleton variant="rectangular" height={200} />
              </ImageListItem>
            ))}
          </ImageList>
        ) : error ? (
          <Typography color="error">{error}</Typography>
        ) : items.length === 0 ? (
          <Typography color="text.secondary" align="center" sx={{ p: 4 }}>
            No media found in the configured bucket folder. Upload an image or
            video to get started!
          </Typography>
        ) : (
          <ImageList
            sx={{ width: '100%', height: 450 }}
            cols={3}
            rowHeight={200}
          >
            {items.map((item) => (
              <MediaItem key={item.uri} item={item} onSelect={onSelect} />
            ))}
          </ImageList>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
      </DialogActions>
    </Dialog>
  );
};

export default CloudStoragePicker;
