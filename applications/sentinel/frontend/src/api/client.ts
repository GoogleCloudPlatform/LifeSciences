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

import axios from 'axios';
import { AnalysisResult, AnalyzePayload, StorageItem } from '../types';

const client = axios.create({
  baseURL: '/api/v1',
  headers: {
    'Content-Type': 'application/json',
  },
});

export const analyzeContent = async (
  payload: AnalyzePayload,
): Promise<AnalysisResult> => {
  const response = await client.post('/analyze', payload);
  return response.data;
};

export const getStorageFiles = async (): Promise<StorageItem[]> => {
  const response = await client.get('/storage/list');
  return response.data.items;
};

export const uploadStorageFile = async (file: File): Promise<StorageItem> => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await client.post('/storage/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
  return response.data;
};

export const deleteStorageFile = async (filePath: string): Promise<void> => {
  // filePath should be the full path within the bucket, e.g., "dev/image.jpg"
  // The backend endpoint is /storage/file/{file_path}
  // We need to encode the path to handle slashes correctly in the URL if not automatically handled
  // However, axios usually handles parameters well, but for path params we construct it manually.
  // Double encoding might be needed if the backend doesn't handle "/" in path params well without it,
  // but FastAPI {path} handles it.
  await client.delete(`/storage/file/${filePath}`);
};
